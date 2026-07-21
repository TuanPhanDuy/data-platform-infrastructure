# data-platform-infrastructure

The **GitOps / infrastructure repo** for the [data-platform](https://github.com/TuanPhanDuy)
semantic platform (`cube-semantic`, `semantic-service`, `chat-service`, `iam-service`,
`data-platform-ui`). Kept separate from those application-source repos on purpose: ArgoCD watches
*this* repo, not each app's source — app CI pipelines only ever end with a commit here, never a
direct cluster mutation.

```
data-platform-infrastructure/
├── helm/data-platform/     # the Helm chart ArgoCD deploys — see helm/README.md
├── argocd/                 # one ArgoCD Application per SDLC stage (dev/staging/prod), all
│                            # pointing back at this repo, each with its own values overlay
├── observability/          # Prometheus/Tempo/Loki/Grafana stack + Kubernetes wiring notes
└── docs/
    ├── HLD.md               # architecture: the full PR -> validate -> build -> sync -> ArgoCD loop
    └── LLD.md                # API contracts, Helm values, ArgoCD spec, CI job graphs
```

## What this repo is for

1. **The desired state of the cluster** — the Helm chart under `helm/data-platform/` (services,
   including Cube.dev itself with its model baked in per-commit — see `docs/HLD.md`).
2. **The GitOps entry point** — `argocd/app-data-platform.yaml`, the ArgoCD `Application` that
   makes (1) real: pull-based, automated sync, self-healing against manual drift.
3. **Observability config** — dashboards, scrape config, and the Kubernetes wiring notes for
   metrics/traces/logs across every service.

## Bootstrap (once, per cluster)

```bash
# 1. Install ArgoCD
kubectl create namespace argocd
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
kubectl -n argocd wait --for=condition=available --timeout=300s deploy/argocd-server

# 2. Point it at this repo (dev instance; swap in app-data-platform-staging.yaml /
#    app-data-platform-prod.yaml for the other SDLC stages — see argocd/)
kubectl apply -f argocd/app-data-platform-dev.yaml

# 3. Watch it reconcile
kubectl get application data-platform-dev -n argocd -w
kubectl get pods -n data-platform
```

From here on, changes to this repo (a Helm values edit, or `cube-semantic`'s CI bumping
`services.cube.image.tag` after a validated model change) are the *only* way the cluster changes
— see `docs/HLD.md` for why, and `docs/LLD.md` for exactly how the local `kind` cluster proof
for this was run.

## Promoting a change through dev → staging → prod

All three ArgoCD `Application`s in `argocd/` watch the same `main` branch of this repo — the
environment split is entirely in which values overlay each one loads
(`values-dev.yaml` / `values-staging.yaml` / `values-prod.yaml`), not a different git ref:

- **dev**: self-contained (in-cluster Redis/Postgres), Cube in-cluster, floating image tags.
- **staging**: external managed backing services, Cube in-cluster and floating — so
  `cube-semantic`'s CI-driven `services.cube.image.tag` bump on `values.yaml` (see `docs/HLD.md`)
  reaches staging automatically, making it the proving ground for a model change.
- **prod**: external managed backing services, `values-prod.yaml` pins every
  `services.*.image.tag` explicitly. Promotion to prod is a deliberate, reviewed PR bumping one
  of those pinned tags — not an automatic consequence of merging to `main`.

## Where things come from

- `helm/data-platform/` — moved from the former `deploy/helm/data-platform/` (see `helm/README.md`).
- `observability/` — moved as-is; still self-contained (`compose-observability.yaml` for local,
  Kubernetes notes in `observability/README.md`).
- `argocd/` and `docs/` are new — the GitOps + model-CI/CD work this repo exists for.
