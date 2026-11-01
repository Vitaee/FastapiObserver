# Sending data to your backend

`fastapi-observer` produces logs, metrics and traces in open formats, so it is not tied to any vendor:

| Data | Format | How it leaves the app |
|---|---|---|
| Logs | JSON lines on stdout, and optionally OTLP | A log collector reads stdout, or set `OBS_OTEL_LOGS_ENABLED=true` with `OBS_OTEL_LOGS_MODE=both` or `otlp` |
| Metrics | Prometheus text at `/metrics`, and optionally OTLP | Prometheus scrapes it, or set `OBS_OTEL_METRICS_ENABLED=true` |
| Traces | OTLP | `OBS_OTEL_ENABLED=true` and `OTEL_EXPORTER_OTLP_ENDPOINT` |

## Logs from stdout

Any collector that reads container output and parses JSON works: Grafana Alloy or Promtail for Loki, Vector, Fluent Bit, the Datadog Agent, the OpenTelemetry Collector's `filelog` receiver, or your cloud provider's logging agent. Each line is one JSON object, so no pattern parsing is needed, and `request_id` and `trace_id` are fields you can search and join on.

## OTLP

Point the OTLP endpoint at an [OpenTelemetry Collector](https://opentelemetry.io/docs/collector/) (recommended) or directly at a backend that accepts OTLP:

```bash
export OBS_OTEL_ENABLED=true
export OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317

# Optional: also export logs over OTLP, keeping JSON on stdout
export OBS_OTEL_LOGS_ENABLED=true
export OBS_OTEL_LOGS_MODE=both          # local_json (default), otlp or both
export OTEL_EXPORTER_OTLP_LOGS_ENDPOINT=http://otel-collector:4317
```

All OpenTelemetry settings are listed in the [configuration guide](configuration.md) and the [OpenTelemetry guide](opentelemetry.md).

## Grafana: Loki, Tempo and Prometheus

- [`examples/full_stack`](https://github.com/Vitaee/FastapiObserver/tree/main/examples/full_stack) is a Docker Compose stack with three services, an OpenTelemetry Collector, Prometheus, Loki, Tempo and a ready-made Grafana dashboard. Screenshots are on the [advanced operations](advanced.md) page.
- [Production deployment](deployment.md) shows the Collector configuration for this stack and what to do when one part of it is down.

## Datadog, Honeycomb and other vendors

Most observability vendors accept OTLP, either directly or through their own agent or collector. Point `OTEL_EXPORTER_OTLP_ENDPOINT` at it and follow the vendor's OTLP setup, for example [Datadog's OpenTelemetry documentation](https://docs.datadoghq.com/opentelemetry/). Logs on stdout can be collected by the vendor's agent in the same way as any other JSON logs.
