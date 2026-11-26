# data-platform GitOps — Low-Level Design (LLD)

Implementation detail for **[HLD.md](./HLD.md)**: the Helm values and templates for
`semantic-service` (database, StarRocks, SQL gateway), Superset's connection to the gateway, the
ArgoCD `Application` spec, and how the chart is checked.

---

## 1. `semantic-service` (values.yaml)

```yaml
semantic-service:
  port: 8081
  extraPorts:
    - { name: pgwire, port: 15432 }      # SQL gateway (Postgres wire) for BI tools
  env:                                   # values pass through `tpl`, so they may use {{ .Release.Name }}
    DB_URL: "jdbc:postgresql://semantic-db:5432/semantic"   # its OWN database (HLD D10)
    DB_USER: "semantic"
    SR_JDBC_URL: "jdbc:mysql://starrocks:9030/information_schema?useSSL=false&allowPublicKeyRetrieval=true&tinyInt1isBit=false"
    SR_USER: "semantic_reader"
    SQL_GATEWAY_PORT: "15432"
  envSecret:                             # env name -> appSecret key
    DB_PASSWORD: SEMANTIC_DB_PASSWORD
    SR_PASSWORD: SR_PASSWORD
    SQL_GATEWAY_READER_PASSWORD: SQL_GATEWAY_READER_PASSWORD
  sqlGateway:
    tlsSecret: ""                        # Secret with keystore.p12 + password; required for sqlExternal
  sqlExternal:                           # LoadBalancer/NodePort for the pgwire port only (HLD D9)
    enabled: false
    type: LoadBalancer
    annotations: {}
    loadBalancerSourceRanges: []
    nodePort: null
```

- **Database.** `DB_URL`/`DB_USER`/`DB_PASSWORD` as per-service env win over the shared
  ConfigMap's `DB_URL` (iam-service's). The user needs `CREATEDB`/`CREATEROLE` for the gateway's
  per-tenant catalog databases (`semantic-service/docs/LLD.md` §3.7).
- **StarRocks.** A read-only account limited to the schemas the model reads.
- **TLS.** When `sqlGateway.tlsSecret` is set, the pod mounts it at `/etc/sql-gateway` and gets
  `SQL_GATEWAY_TLS_KEYSTORE=/etc/sql-gateway/keystore.p12` and `SQL_GATEWAY_TLS_KEYSTORE_PASSWORD`
  from the Secret's `password` key; every gateway login must then use TLS. Create it with
  `kubectl create secret generic sql-gateway-tls --from-file=keystore.p12 --from-literal=password=…`
  (a PKCS#12 from cert-manager's `keystores.pkcs12` works too).
- **Secrets** (`appSecret.data`, or the pre-provisioned Secret in staging/prod):
  `SEMANTIC_DB_PASSWORD`, `SR_PASSWORD`, `SQL_GATEWAY_READER_PASSWORD` (the read-only role the
  service creates for its catalog databases), and `SUPERSET_SEMANTIC_SQL_PASSWORD` (§3).

Overlays: `values-dev.yaml` points the database at the in-cluster Postgres
(`jdbc:postgresql://{{ .Release.Name }}-postgresql:5432/semantic`, user and password of
`infra.postgresql`, which is a superuser there), StarRocks at `host.docker.internal:9030`, and sets
`SQL_GATEWAY_ALLOW_PLAINTEXT=true` (no TLS on a laptop); `values-staging.yaml` turns `sqlExternal`
on with a TLS secret; `values-prod.yaml` sets managed endpoints and keeps `sqlExternal` off until a
network policy for BI clients exists.

---

## 2. Template changes

| Template | Change |
|---|---|
| `configmap.yaml` | Drops `CUBE_*`/`CUBEJS_*`. |
| `deployment.yaml` | `env` values go through `tpl`; when `$svc.sqlGateway.tlsSecret` is set, mounts it and adds the two keystore env vars. |
| `sql-gateway-external.yaml` | Replaces `cube-sql-external.yaml`: a Service named `<release>-semantic-sql-external` selecting semantic-service pods, port 15432 → `pgwire`; rendered only when `services.semantic-service.sqlExternal.enabled`. |
| `infra-postgres.yaml` | The init script always creates the `semantic` database (and `superset` when Superset is on), so the in-cluster Postgres serves semantic-service in dev. |
| `superset-deployment.yaml` | Registers the gateway instead of Cube (§3). |
| `NOTES.txt` | Prints the gateway endpoint instead of Cube's. |

`services.cube`, `config.cube*`, `CUBE_API_SECRET` and `CUBE_SQL_PASSWORD` are removed.

---

## 3. Superset → SQL gateway

`superset.semantic`:
```yaml
semantic:
  registerConnection: true
  connectionName: "Semantic layer"
  username: "acme/superset"     # a gateway account of the tenant Superset serves
```
The init container runs, when `SUPERSET_SEMANTIC_SQL_PASSWORD` is present in the Secret (the
`secretKeyRef` is optional):
```
superset set-database-uri --database_name "Semantic layer" \
  --uri "postgresql+psycopg2://acme%2Fsuperset:$PASS@<release>-semantic-service:15432/semantic?sslmode=<require|disable>"
```
`sslmode=require` when `services.semantic-service.sqlGateway.tlsSecret` is set, else `disable`
(only valid with `SQL_GATEWAY_ALLOW_PLAINTEXT=true`, i.e. dev). The `/` of the username is
URL-encoded. Bootstrap order: deploy → publish a model → create the account
(`POST /api/semantic/sql-credentials {"name":"superset"}`) → put its password in the Secret →
restart the Superset pod.

---

## 4. ArgoCD `Application`s (`argocd/app-data-platform-{dev,staging,prod}.yaml`)

One `Application` per SDLC stage, all watching this repo's `main` — the split is which values
overlay each loads, not a different git ref:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: data-platform-dev   # -staging / -prod for the siblings
spec:
  source:
    repoURL: https://github.com/TuanPhanDuy/data-platform-infrastructure.git
    targetRevision: main
    path: helm/data-platform
    helm:
      valueFiles: [values.yaml, values-dev.yaml]   # -staging.yaml / -prod.yaml for the siblings
  destination:
    server: https://kubernetes.default.svc
    namespace: data-platform
  syncPolicy:
    automated: { prune: true, selfHeal: true }
    syncOptions: [CreateNamespace=true]
```
`prune: true` deletes what the chart no longer renders — syncing this change removes the Cube
Deployment, its Services and `cube-sql-external`. `selfHeal: true` reverts manual drift.
Staging floats on `values.yaml`'s tags; `values-prod.yaml` pins `services.*.image.tag`, so
promoting to prod is a deliberate PR against that file.

---

## 5. Checking the chart

No CI runs for this repo yet; before pushing, render every overlay:

```bash
for env in dev staging prod; do
  helm lint helm/data-platform -f helm/data-platform/values-$env.yaml
  helm template dp helm/data-platform -f helm/data-platform/values-$env.yaml > /tmp/dp-$env.yaml
done
grep -c -i cube /tmp/dp-*.yaml                                  # 0: nothing of Cube is rendered
grep -A3 'name: pgwire' /tmp/dp-staging.yaml                     # container + Service ports
```
