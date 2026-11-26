# Helm chart

Container images + a Helm chart to run the data-platform application services with horizontal
autoscaling. This chart is what ArgoCD syncs — see
[`../argocd/`](../argocd/) and [`../docs/HLD.md`](../docs/HLD.md) for how it gets there.

```
helm/
└── data-platform/     # Helm chart (Chart.yaml, values*.yaml, templates/)
```

## What gets deployed

| Service            | Port  | Probe                         | Autoscaling (default) |
|---------------------|-------|--------------------------------|------------------------|
| semantic-service    | 8081 (+ 15432 SQL gateway) | HTTP `/actuator/health` | 2–6 pods, CPU 70% |
| chat-service        | 8082  | HTTP `/actuator/health`        | 2–8 pods, CPU 65%      |
| iam-service         | 8084  | HTTP `/actuator/health/{liveness,readiness}` | 2–6 pods, CPU 70% |
| data-platform-ui    | 8080  | HTTP `/healthz` (nginx)        | 2–4 pods, CPU 80%      |

Each service gets a Deployment, ClusterIP Service, HorizontalPodAutoscaler and
PodDisruptionBudget. A shared ConfigMap injects release-aware URLs (e.g. chat → semantic, and
Redis/Postgres hosts when in-cluster infra is on); a Secret holds credentials; per-service `env`/
`envSecret` override them (semantic-service sets its own database and StarRocks this way). Two Ingresses expose the UI at `/` and the APIs under `/api/*`
(prefix stripped before forwarding) — or Kong, via `gateway.provider: kong` (Kong config itself
lives in the `data-platform` app-source checkout's `gateway/` folder, not in this repo).

### `semantic-service`: the semantic layer

It owns the semantic model in its own Postgres (edited and published through `/api/semantic/**`,
not deployed from Git), runs every query on StarRocks, and serves BI tools on port 15432. Set its
database and StarRocks under `services.semantic-service.env`/`envSecret`; see `docs/LLD.md` §1.

### Backing services (Postgres, Redis, StarRocks, Keycloak)
External by default — best practice for stateful systems. Point `config.*` and
`services.semantic-service.env` at managed offerings (managed Postgres, managed Redis, a Keycloak
operator or managed IdP). StarRocks is never bundled — run it with a dedicated operator (or a
host docker-compose in dev) and set `SR_JDBC_URL` to reach it. For local testing,
`values-dev.yaml` turns on throwaway in-cluster Redis + Postgres (which also hosts
semantic-service's database).

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

## 2. Deploy

Local (kind, self-contained app-service infra, pointed at a host-run StarRocks):
```bash
helm upgrade --install dp ./helm/data-platform -f ./helm/data-platform/values-dev.yaml
```

Staging (external infra, the SQL gateway exposed with TLS):
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

Kong validates the access token and forwards only its subject as `X-Idp-Sub`; each service checks
permissions with iam-service. Every service is reachable as `/svc/<service>/<native path>`, and
its native paths too where no other service shares them (`gateway.routes`; the table is in the
app-source checkout's `gateway/README.md`), so build the UI with `VITE_<SERVICE>_API=/svc/<service>`.
The sandbox needs only `untrusted_lua_sandbox_requires=cjson.safe`.

`IAM_INTERNAL_TOKEN` in the app Secret guards iam-service's in-cluster `/internal` subject lookup:
set a random value, including in the pre-provisioned staging/prod Secrets. `networkPolicy.iam`
(on in staging and prod) lets only this release's pods and the Kong namespace
(`networkPolicy.iam.gatewayNamespace`) reach iam-service; add a Prometheus namespace to
`networkPolicy.iam.extraFrom` if it scrapes iam-service.

## BI tool connections (Superset, Power BI, Tableau, Metabase, Supabase)

semantic-service's SQL gateway speaks the Postgres wire protocol: any tool with a Postgres
connector sees the tenant's cubes and views as tables (dimensions and measures as columns) and
queries them with plain SQL. Three prerequisites:

1. **A published model and a tenant account.** A tenant admin creates a service account:
   ```bash
   curl -s -X POST https://<host>/api/semantic/sql-credentials -H 'Content-Type: application/json' \
     -H "Authorization: Bearer $TENANT_ADMIN_TOKEN" -d '{"name":"powerbi"}'   # needs semantic:manage
   # → {"username":"acme/powerbi","password":"sk_…"}   shown once; revoke with DELETE …/{id}
   ```
2. **TLS.** Mount a PKCS#12 keystore with `services.semantic-service.sqlGateway.tlsSecret`
   (`docs/LLD.md` §1); every login must then use TLS. Without one, only loopback clients can log in
   unless `SQL_GATEWAY_ALLOW_PLAINTEXT=true` (dev).
3. **A route to port 15432.** In-cluster it is `<release>-semantic-service:15432`. From outside:
   `kubectl port-forward svc/<release>-semantic-service 15432:15432` (dev), or
   `services.semantic-service.sqlExternal.enabled: true` (staging) and
   `kubectl get svc <release>-semantic-sql-external -w` for the external IP.

### Superset (self-hosted, in-cluster)

`superset.enabled: true` deploys Superset with its own metadata Postgres and, on start, an init
container that runs `superset db upgrade`, creates the admin user, and registers the gateway as a
database connection named `superset.semantic.connectionName` for the account
`superset.semantic.username` — once that account's password is in the Secret under
`SUPERSET_SEMANTIC_SQL_PASSWORD`. Order on a fresh cluster: deploy, publish a model, create the
account (prerequisite 1 with `"name":"superset"`), store the password, restart the Superset pod.
Then go to Data > Datasets and pick a cube or view.

### Power BI Desktop

1. **Get Data > Database > PostgreSQL database.**
2. **Server**: the port-forwarded/LoadBalancer host and port, e.g. `localhost:15432`.
   **Database**: any name (the account decides the tenant), e.g. `semantic`.
3. **Import** or **DirectQuery**. Queries are translated to the semantic model, so a measure keeps
   its own aggregation whatever Power BI wraps around it (ratios stay correct).
4. Credentials: **Database** auth, user `acme/powerbi`, the password from prerequisite 1. Keep
   **Encrypt connection** on (the gateway requires TLS once a keystore is configured).
5. The cubes and views appear as tables in the Navigator.

### Others

Tableau, Metabase, DBeaver, Excel (psqlODBC) and psql use their PostgreSQL connector the same
way. Supabase reads the model through `postgres_fdw`: `CREATE SERVER semantic FOREIGN DATA
WRAPPER postgres_fdw OPTIONS (host '…', port '15432', dbname 'semantic', sslmode 'require')`, a
user mapping with the account, then `IMPORT FOREIGN SCHEMA public FROM SERVER semantic INTO
semantic`. What the gateway translates: `semantic-service/docs/LLD.md` §3.7.

## Observability

See [`../observability/README.md`](../observability/README.md). The chart's `observability:`
values control Prometheus scrape annotations vs ServiceMonitors, the in-cluster OTel Collector,
OTLP endpoint/sampling, and the `json` log profile.

## Prerequisites
- Kubernetes 1.23+ (autoscaling/v2), an ingress controller (nginx assumed), metrics-server, and
  a StorageClass if you enable in-cluster Postgres persistence.
