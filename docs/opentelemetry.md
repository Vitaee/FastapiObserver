# OpenTelemetry (Traces + Optional OTLP Logs + Optional OTLP Metrics)

```python
from fastapiobserver import (
    OTelLogsSettings,
    OTelMetricsSettings,
    OTelSettings,
    install_observability,
)

otel_settings = OTelSettings(
    enabled=True,
    service_name="orders-api",
    service_version="2.0.0",
    environment="production",
    otlp_endpoint="http://localhost:4317",
    protocol="grpc",                  # or "http/protobuf"
    trace_sampling_ratio=1.0,
    extra_resource_attributes={
        "k8s.namespace": "prod",
        "team": "backend",
    },
)

otel_logs_settings = OTelLogsSettings(
    enabled=True,
    logs_mode="both",                 # "local_json", "otlp", or "both"
    otlp_endpoint="http://localhost:4317",
    protocol="grpc",
)

otel_metrics_settings = OTelMetricsSettings(
    enabled=True,
    otlp_endpoint="http://localhost:4317",
    protocol="grpc",                  # or "http/protobuf"
    export_interval_millis=60000,
)

install_observability(
    app,
    settings,
    otel_settings=otel_settings,
    otel_logs_settings=otel_logs_settings,
    otel_metrics_settings=otel_metrics_settings,
)
```

Design details:
- Reuses an externally configured tracer provider if one already exists.
- Injects trace IDs into application logs for log-trace correlation.
- Supports runtime sampling updates through the control plane.
- Sends OTel logs in OTLP mode with the same sanitization policy.
- Supports optional OTLP metrics export for unified OTel backends.
- Registers graceful shutdown hooks to flush provider buffers on app exit.

### Baggage propagation

`inject_trace_headers()` uses OpenTelemetry propagation, so it forwards
`traceparent`, `tracestate`, and `baggage` when baggage is present in the active context.

```python
from opentelemetry import baggage
from opentelemetry.context import attach, detach

from fastapiobserver import inject_trace_headers

token = attach(baggage.set_baggage("tenant_id", "acme"))
try:
    headers = inject_trace_headers({})
    # headers["baggage"] == "tenant_id=acme"
finally:
    detach(token)
```

---

