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

Unlike the other services, `cube`'s image isn't built by hand — `cube-semantic-demo`'s
`.github/workflows/model-ci.yml` bakes the validated Cube YAML model into an image and bumps
`services.cube.image.tag` in this chart's `values.yaml` as its last step. **That commit is the
deploy** — see [`../docs/HLD.md`](../docs/HLD.md) for the full loop. StarRocks/Postgres (what
Cube queries) stay external either way — point `config.cubeDb*` at wherever StarRocks runs.

### Backing services (Postgres, Redis, StarRocks, Keycloak)
External by default — best practice for stateful systems. Point `config.*` at managed offerings
(managed Postgres, ElastiCache/managed Redis, a Keycloak operator or managed IdP). StarRocks (and
the Postgres it federates from) is never bundled — run it via `cube-semantic-demo`'s
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
`cube`'s image is built by `cube-semantic-demo`'s CI, not by hand — see above.

## 2. Deploy

Local (kind, self-contained app-service infra; Cube in-cluster too, pointed at a host-run
StarRocks — see `docs/LLD.md` for the exact commands used to prove this with a real kind cluster):
```bash
helm upgrade --install dp ./helm/data-platform -f ./helm/data-platform/values-dev.yaml
```

Production (external infra, images from a private registry, pre-created secret):
```bash
kubectl create namespace data-platform
# provision the Secret named in values-prod.yaml (appSecret.name) out of band first
helm upgrade --install dp ./helm/data-platform -n data-platform -f ./helm/data-platform/values-prod.yaml
```

In practice neither command runs by hand against a real cluster — ArgoCD runs the equivalent
`helm upgrade` for you on every reconcile. See [`../argocd/`](../argocd/).

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

## Observability

See [`../observability/README.md`](../observability/README.md). The chart's `observability:`
values control Prometheus scrape annotations vs ServiceMonitors, the in-cluster OTel Collector,
OTLP endpoint/sampling, and the `json` log profile.

## Prerequisites
- Kubernetes 1.23+ (autoscaling/v2), an ingress controller (nginx assumed), metrics-server, and
  a StorageClass if you enable in-cluster Postgres persistence.
