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
- On FastAPI 0.142 and later, requests are traced by FastAPI's own OpenTelemetry support, with spans for dependency resolution, the endpoint and serialization. Older versions, or `FastAPI(telemetry={"tracing": False})`, use OpenTelemetry's FastAPI instrumentation. See [decision 0010](decisions/0010-trace-with-fastapis-own-opentelemetry.md).
- A failed request's server span has `error.type` and an `ERROR` status. The exception message and stack trace stay in the redacted `request.failed` log line, which carries the same `trace_id`.
- When OTLP log export is on, FastAPI's own error log export is turned off, so error logs reach the backend once, redacted.
- Reuses an externally configured tracer provider if one already exists.
- Injects trace IDs into application logs for log-trace correlation.
- Supports runtime sampling updates through the control plane.
- Sends OTel logs in OTLP mode with the same sanitization policy.
- Supports optional OTLP metrics export for unified OTel backends.
- Flushes and shuts down its providers when the app's lifespan ends, after the log queue has drained; on process exit the SDK's own `atexit` hook takes over.

### Request ID forwarding

Outbound calls carry the current request ID, so a request keeps one ID across every service it passes through, and searching your logs for that ID finds all of them. This works without tracing turned on.

- `inject_trace_headers()` adds it as `REQUEST_ID_HEADER` (`x-request-id` by default). Without OpenTelemetry installed, that is the only header it adds.
- With the `[otel]` extra, `install_observability()` adds the request ID to OpenTelemetry's global propagator, so clients it instruments, such as `instrument_httpx_client()` and `instrument_requests_session()`, send it next to `traceparent`.
- A request ID header you set yourself is kept.

The next service accepts the forwarded ID only from addresses in `OBS_TRUSTED_CIDRS`. The default list includes the private ranges that pod and VPC networks use; if your services call each other from other addresses, add them there. Like `traceparent`, the header is also sent to third-party APIs you call through an instrumented client.

### Other HTTP clients

`instrument_httpx_client()` and `instrument_requests_session()` wrap OpenTelemetry's instrumentations for `httpx` and `requests`. For another client, install OpenTelemetry's instrumentation for it, such as `opentelemetry-instrumentation-aiohttp-client` or `opentelemetry-instrumentation-urllib3`. Every OpenTelemetry instrumentation reads the same global propagator, so it sends `traceparent` and the request ID too.

A client without one, such as `httpx2`, needs a request hook:

```python
import httpx2
from opentelemetry import propagate


async def forward_context(request: httpx2.Request) -> None:
    propagate.inject(request.headers)


client = httpx2.AsyncClient(event_hooks={"request": [forward_context]})
```

A synchronous `httpx2.Client` takes the same hook as a plain function. Without the `[otel]` extra, pass `headers=inject_trace_headers({})` on each call instead.

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

