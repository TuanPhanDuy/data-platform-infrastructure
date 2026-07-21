# Helm chart

Container images + a Helm chart to run the data-platform application services (including
Cube.dev itself) with horizontal autoscaling. This chart is what ArgoCD syncs — see
[`../argocd/`](../argocd/) and [`../docs/HLD.md`](../docs/HLD.md) for how it gets there.

```
helm/
└── data-platform/     # Helm chart (Chart.yaml, values*.yaml, templates/)
```

## What gets deployed

| Service            | Port  | Probe                         | Autoscaling (default) |
|---------------------|-------|--------------------------------|------------------------|
| semantic-service    | 8081  | HTTP `/actuator/health`        | 2–6 pods, CPU 70%      |
| chat-service        | 8082  | HTTP `/actuator/health`        | 2–8 pods, CPU 65%      |
| iam-service         | 8084  | HTTP `/actuator/health/{liveness,readiness}` | 2–6 pods, CPU 70% |
| data-platform-ui    | 8080  | HTTP `/healthz` (nginx)        | 2–4 pods, CPU 80%      |
| cube                | 4000 (+ 15432 SQL API) | HTTP `/readyz`  | disabled — Cube dev-mode is single-instance |

Each service gets a Deployment, ClusterIP Service, and (except `cube`) a
HorizontalPodAutoscaler + PodDisruptionBudget. A shared ConfigMap injects release-aware URLs
(e.g. chat → semantic, semantic → cube, and Redis/Postgres hosts when in-cluster infra is on); a
Secret holds credentials. Two Ingresses expose the UI at `/` and the APIs under `/api/*`
(prefix stripped before forwarding) — or Kong, via `gateway.provider: kong` (Kong config itself
lives in the `data-platform` app-source checkout's `gateway/` folder, not in this repo).

### `cube`: baked-in model, CI-driven tag

Unlike the other services, `cube`'s image isn't built by hand — `cube-semantic`'s
`.github/workflows/model-ci.yml` bakes the validated Cube YAML model into an image and bumps
`services.cube.image.tag` in this chart's `values.yaml` as its last step. **That commit is the
deploy** — see [`../docs/HLD.md`](../docs/HLD.md) for the full loop. StarRocks/Postgres (what
Cube queries) stay external either way — point `config.cubeDb*` at wherever StarRocks runs.

### Backing services (Postgres, Redis, StarRocks, Keycloak)
External by default — best practice for stateful systems. Point `config.*` at managed offerings
(managed Postgres, ElastiCache/managed Redis, a Keycloak operator or managed IdP). StarRocks (and
the Postgres it federates from) is never bundled — run it via `cube-semantic`'s
docker-compose, or a dedicated operator, and set `config.cubeDb*` to reach it. For local testing,
`values-dev.yaml` turns on throwaway in-cluster Redis + Postgres for the *app* services.

## 1. Build & push images

```bash
docker build -t $REG/data-platform/semantic-service:1.0.0 semantic-service
docker build -t $REG/data-platform/chat-service:1.0.0     chat-service
docker build -t $REG/data-platform/iam-service:1.0.0 -f iam-service/Dockerfile.jvm iam-service
docker build -t $REG/data-platform/data-platform-ui:1.0.0 \
  --build-arg VITE_SEMANTIC_API= --build-arg VITE_CHAT_API= --build-arg VITE_INGESTION_API= \
  data-platform-ui
docker push $REG/data-platform/...
```
`cube`'s image is built by `cube-semantic`'s CI, not by hand — see above.

## 2. Deploy

Local (kind, self-contained app-service infra; Cube in-cluster too, pointed at a host-run
StarRocks — see `docs/LLD.md` for the exact commands used to prove this with a real kind cluster):
```bash
helm upgrade --install dp ./helm/data-platform -f ./helm/data-platform/values-dev.yaml
```

Staging (external infra, Cube stays in-cluster so `cube-semantic`'s CI-driven tag bump reaches
it automatically — see `docs/HLD.md`):
```bash
kubectl create namespace data-platform-staging
helm upgrade --install dp ./helm/data-platform -n data-platform-staging -f ./helm/data-platform/values-staging.yaml
```

Production (external infra, images from a private registry, pre-created secret, pinned tags):
```bash
kubectl create namespace data-platform
# provision the Secret named in values-prod.yaml (appSecret.name) out of band first
helm upgrade --install dp ./helm/data-platform -n data-platform -f ./helm/data-platform/values-prod.yaml
```

In practice none of these commands run by hand against a real cluster — ArgoCD runs the
equivalent `helm upgrade` for you on every reconcile, one `Application` per stage. See
[`../argocd/`](../argocd/).

## 3. Verify & watch scaling

```bash
kubectl get pods,svc,hpa -l app.kubernetes.io/part-of=data-platform
kubectl get hpa -w
```
Access: add the ingress host (default `data-platform.local`) to `/etc/hosts` pointing at your
ingress controller, then open `http://data-platform.local/`.

## Scaling model

- **HPA** on CPU + memory per service, with tuned `behavior`: fast scale-up (double every 30s),
  cautious scale-down (1 pod/60s, 5-min stabilization).
- **PDB** (`minAvailable: 1`) keeps a pod up during node drains/upgrades.
- **topologySpreadConstraints** spread replicas across nodes.
- HPA requires **metrics-server** in the cluster (`kubectl top pods` must work).
- Tune floors/ceilings and CPU targets per service in `values*.yaml`.

## API gateway (Kong)

By default (`gateway.provider: kong`) the chart fronts the services with **Kong** via the Kong
Ingress Controller instead of the plain nginx Ingress. Set `gateway.provider: nginx` to fall back.

## BI tool connections (Superset, Power BI)

Cube's SQL API speaks the Postgres wire protocol — any tool with a Postgres connector can query
it, and cubes show up as tables (measures/dimensions as columns). Two prerequisites, both handled
by this chart:

1. **The SQL API must actually be enabled.** Cube Core has it off by default; `services.cube.env`
   sets `CUBEJS_PG_SQL_PORT`/`CUBEJS_SQL_USER` and `envSecret` maps `CUBEJS_SQL_PASSWORD` from
   `appSecret.data.CUBE_SQL_PASSWORD` (values.yaml). Credentials: user = `config.cubeSqlUser`
   (default `cube`), password = that secret key, database = literal `cube` (Cube Core ignores the
   db name — any string works, this chart's convention matches `config.cubeSqlUrl`).
2. **Something outside the cluster needs to reach port 15432.** The chart's normal Service is
   ClusterIP-only. `values-dev.yaml`: `kubectl port-forward svc/<release>-cube 15432:15432`.
   `values-staging.yaml`: `services.cube.sqlExternal.enabled: true` (LoadBalancer) — get the
   external IP with `kubectl get svc <release>-cube-sql-external -w`.

### Superset (self-hosted, in-cluster)

`superset.enabled: true` (already on in `values-dev.yaml`/`values-staging.yaml`) deploys Superset
with its own metadata Postgres (a second database on `infra.postgresql`'s instance in dev, an
external one via `superset.metadataDbUrl` in staging/prod) and, on first boot, an init container
that runs `superset db upgrade`, creates the admin user (`superset.admin.*` +
`appSecret.data.SUPERSET_ADMIN_PASSWORD`), and pre-registers Cube as a database connection
(`superset set-database-uri`, named `superset.cube.connectionName`) — nothing to configure by
hand. Reach the UI via `kubectl port-forward svc/<release>-superset 8088:8088` (dev) or
`superset.ingress.host` (staging), log in with the admin credentials, and the "Cube" database is
already there under Data > Databases — go straight to Data > Datasets to build one against a cube.

Set `superset.cube.registerConnection: false` to skip the auto-registration and add the
connection by hand instead (Data > Databases > + Database > PostgreSQL), using the same
host/port/user/password/db as above (in-cluster host is `<release>-cube`, e.g. `dp-cube`).

### Power BI Desktop

Power BI Desktop is a client app — there's nothing to deploy for it, it just needs network
access to port 15432 (see prerequisite 2 above) and the native PostgreSQL connector:

1. **Get Data > Database > PostgreSQL database.**
2. **Server**: the port-forwarded/LoadBalancer host, e.g. `localhost:15432` or the external IP
   from `kubectl get svc <release>-cube-sql-external`. **Database**: `cube`.
3. Pick **Import** or **DirectQuery** (DirectQuery keeps queries live against Cube but means
   re-syncing the semantic model manually as cubes change; Import is simpler to start with).
4. Credentials: **Database** auth, user = `config.cubeSqlUser` (`cube`), password = the
   `CUBE_SQL_PASSWORD` secret value (`kubectl get secret <release>-secret -o jsonpath='{.data.CUBE_SQL_PASSWORD}' | base64 -d`).
   Cube's SQL API doesn't terminate TLS by default, so leave **Encrypt connection** off unless
   you've put a TLS-terminating proxy in front of it.
5. Your cubes appear as tables in the Navigator — measures and dimensions as columns.

## Observability

See [`../observability/README.md`](../observability/README.md). The chart's `observability:`
values control Prometheus scrape annotations vs ServiceMonitors, the in-cluster OTel Collector,
OTLP endpoint/sampling, and the `json` log profile.

## Prerequisites
- Kubernetes 1.23+ (autoscaling/v2), an ingress controller (nginx assumed), metrics-server, and
  a StorageClass if you enable in-cluster Postgres persistence.
