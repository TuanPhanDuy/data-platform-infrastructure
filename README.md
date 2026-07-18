# data-platform-infrastructure

The **GitOps / infrastructure repo** for the [data-platform](https://github.com/TuanPhanDuy)
semantic platform (`cube-semantic-demo`, `semantic-service`, `chat-service`, `iam-service`,
`data-platform-ui`). Kept separate from those application-source repos on purpose: ArgoCD watches
*this* repo, not each app's source — app CI pipelines only ever end with a commit here, never a
direct cluster mutation.

```
data-platform-infrastructure/
├── helm/data-platform/     # the Helm chart ArgoCD deploys — see helm/README.md
├── argocd/                 # the ArgoCD Application CR(s) that point back at this repo
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

# 2. Point it at this repo
kubectl apply -f argocd/app-data-platform.yaml

# 3. Watch it reconcile
kubectl get application data-platform -n argocd -w
kubectl get pods -n data-platform
```

From here on, changes to this repo (a Helm values edit, or `cube-semantic-demo`'s CI bumping
`services.cube.image.tag` after a validated model change) are the *only* way the cluster changes
— see `docs/HLD.md` for why, and `docs/LLD.md` for exactly how the local `kind` cluster proof
for this was run.

## Where things come from

- `helm/data-platform/` — moved from the former `deploy/helm/data-platform/` (see `helm/README.md`).
- `observability/` — moved as-is; still self-contained (`compose-observability.yaml` for local,
  Kubernetes notes in `observability/README.md`).
- `argocd/` and `docs/` are new — the GitOps + model-CI/CD work this repo exists for.
