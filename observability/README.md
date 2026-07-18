# Observability

Metrics, distributed traces, and logs for every data-platform service, cross-
linked in Grafana. All three Java services (`semantic-service`, `chat-service`,
`iam-service`) are instrumented via Spring Boot Actuator + Micrometer.

```
                        ┌──────────────┐
 /actuator/prometheus ─▶│  Prometheus  │─┐
                        └──────────────┘ │
     OTLP traces  ┌──────────────┐ ┌───────────┐   ┌──────────┐
        ─────────▶│OTel Collector│▶│   Tempo   │──▶│          │
                  └──────────────┘ └───────────┘   │ Grafana  │  trace ↔ log
   JSON stdout   ┌──────────┐   ┌──────────┐       │          │  correlation
        ────────▶│ Promtail │──▶│   Loki   │──────▶│          │
                 └──────────┘   └──────────┘       └──────────┘
```

## What each service emits

| Signal   | How                                             | Endpoint / sink            |
|----------|-------------------------------------------------|----------------------------|
| Metrics  | Micrometer → Prometheus registry                | `GET /actuator/prometheus` |
| Traces   | Micrometer Tracing → OTLP exporter              | `→ collector :4318`        |
| Logs     | Logback JSON (`json` profile) w/ traceId/spanId | stdout → Promtail → Loki   |

Sampling, OTLP endpoint and environment are env-driven:
`TRACING_SAMPLE_RATE`, `OTLP_TRACING_ENDPOINT`, `APP_ENV`, and the `json`
Spring profile (turns on structured logging).

## Run locally (apps on host)

1. Start the stack:
   ```bash
   docker compose -f observability/compose-observability.yaml up -d
   ```
2. Run each service pointing traces at the collector and writing JSON logs to
   `observability/logs/` (tailed by Promtail):
   ```bash
   mkdir -p observability/logs
   export OTLP_TRACING_ENDPOINT=http://localhost:4318/v1/traces
   export TRACING_SAMPLE_RATE=1.0
   export SPRING_PROFILES_ACTIVE=json
   java -jar semantic-service/target/semantic-service-1.0.0.jar > observability/logs/semantic-service.log 2>&1 &
   java -jar chat-service/target/chat-service-1.0.0.jar         > observability/logs/chat-service.log 2>&1 &
   java -jar iam-service/target/iam-service-1.0.0.jar           > observability/logs/iam-service.log 2>&1 &
   ```
3. Open Grafana at http://localhost:3000 (anonymous admin). The
   **data-platform overview** dashboard is pre-provisioned. Click a log line's
   `TraceID` to jump to its trace in Tempo; from a span, "Logs for this span"
   opens the correlated Loki logs.

Ports: Grafana 3000 · Prometheus 9091 · Tempo 3200 · Loki 3100 · OTLP 4317/4318.

## Kubernetes

The Helm chart (`../helm/data-platform`) wires observability automatically —
see the `observability:` block in `values.yaml`:

- **Metrics** — pods carry `prometheus.io/scrape` annotations, or set
  `observability.serviceMonitor.enabled=true` to emit ServiceMonitors for the
  Prometheus Operator (kube-prometheus-stack). `values-prod.yaml` uses the latter.
- **Traces** — set `observability.collector.enabled=true` to run an in-cluster
  OTel Collector; `OTLP_TRACING_ENDPOINT` is auto-pointed at it. Otherwise set
  `observability.otlpEndpoint` to an external collector. Point
  `collector.tempoEndpoint` at your Tempo.
- **Logs** — pods log JSON to stdout (the `json` profile is in
  `config.springProfilesActive`). Run **Grafana Alloy / Promtail as a DaemonSet**
  to ship pod stdout to Loki; the format and `traceId` field match this stack.

Recommended backends in a cluster: install **kube-prometheus-stack** (Prometheus
+ Grafana + Alertmanager) and **Grafana Tempo + Loki** (or Grafana Cloud), then
import `grafana/provisioning/.../data-platform-overview.json` and the datasource
correlation config here.

## Notes
- `iam-service` also exposes `/actuator/prometheus` and OTLP tracing (it shipped
  with these); this stack now scrapes all three services, not just iam.
- The `data-platform-ui` (nginx) has no app metrics; its pod stdout logs are
  still collected. Add browser RUM (e.g. Grafana Faro) separately if needed.
- Local logs use Promtail tailing files because the apps run as host processes.
  In Kubernetes, prefer the DaemonSet-against-stdout approach above.
