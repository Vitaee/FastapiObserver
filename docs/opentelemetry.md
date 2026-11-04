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
- Flushes its providers when the app's lifespan ends, after the log queue has drained, and leaves them running for other apps in the process; the SDK's own `atexit` hook shuts them down at process exit.

### Application-owned logging and OpenTelemetry

With `install_observability(app, configure_logging=False)`, the application's
logging handlers, logger provider, log processors and exporters remain in place.
The library does not call `install_otel_logs()` or attach another logging handler
or exporting log processor, regardless of `otel_logs_settings` or the log-export
environment variables. Keep your approved sanitizer on the application-owned
OTel logging path as well as local outputs.

Tracing and metrics keep their own enable flags. When enabled, they reuse
existing providers without adding exporting span processors or metric readers.
Tracing can add the request-call accounting processor, which does not export
spans. An external tracer provider keeps its own sampler. Providers used for
tracing or metrics are flushed at lifespan exit and remain usable afterward;
the application owns its logging provider's lifecycle.

FastAPI's native telemetry configuration is also left intact in this mode.
On FastAPI 0.142+, its direct OTel error logs bypass stdlib handler filters. If
your application routes errors through its own sanitized logging pipeline, use
`FastAPI(telemetry={"logs": False})` as the existing-logger example does, or retain
an approved sanitizer on that separate native export path. This is an application
choice; the library does not silently change it.

### Request ID forwarding

Outbound calls carry the current request ID, so a request keeps one ID across every service it passes through, and searching your logs for that ID finds all of them. This works without tracing turned on.

- `inject_trace_headers()` adds it as `OBS_REQUEST_ID_HEADER` (`x-request-id` by default). Without OpenTelemetry installed, that is the only header it adds.
- With the `[otel]` extra, `install_observability()` adds the request ID to OpenTelemetry's global propagator, so clients it instruments, such as `instrument_httpx_client()` and `instrument_requests_session()`, send it next to `traceparent`.
- A request ID header you set yourself is kept.

The next service accepts the forwarded ID only from addresses in `OBS_TRUSTED_CIDRS`. The default list includes the private ranges that pod and VPC networks use; if your services call each other from other addresses, add them there. Like `traceparent`, the header is also sent to third-party APIs you call through an instrumented client.

### Other HTTP clients

`instrument_httpx_client()` supports HTTPX and HTTPX2, and
`instrument_requests_session()` supports Requests. Both count calls and forward
request IDs without OpenTelemetry. Their optional `[otel-httpx]` and
`[otel-requests]` extras add client spans through OpenTelemetry's instrumentations.
For another client, install its OpenTelemetry instrumentation, such as
`opentelemetry-instrumentation-aiohttp-client` or
`opentelemetry-instrumentation-urllib3`. These read the same global propagator.

For an HTTPX2 client, use the same helper:

```python
import httpx2
from fastapiobserver import instrument_httpx_client

client = httpx2.AsyncClient()
instrument_httpx_client(client)
```

A synchronous client works the same way. Calling the helper without a client
instruments all installed HTTPX and HTTPX2 clients. Requests instrumentation is
global; passing a session remains unsupported. Install clients before serving traffic.

### Outbound HTTP diagnostics

The request line's `http` object contains `count`, `ms`, and an optional `errors`
object. The helpers above record these with tracing off, with sampling at zero,
and with tracing on, counting each call once even when it also creates a span.

- One call is one logical `Client.send()` / `Session.send()` operation. Redirects,
  authentication exchanges and transport retries are included in that operation.
- Timing starts just before the send and ends after a non-streaming response is
  consumed. For streaming responses it ends when the stream is consumed or closed,
  or raises. This includes time the consumer spends between reads. No response body
  is eagerly read for diagnostics. An early close counts once.
- Totals include calls completed or closed before the ASGI application ends.
  Detached work or a stream left open past that point is not part of that line.
- Exceptions propagate unchanged. Counts include failed sends, 4xx/5xx responses,
  streaming failures and cancellation. `errors` uses bounded categories:
  `connect_timeout`, `read_timeout`, `write_timeout`, `pool_timeout`, `timeout`,
  `network_error`, `request_error`, `http_4xx`, `http_5xx`, and `cancelled`.
  A transfer exception takes precedence over its response status. Only categories
  the client exposes can be distinguished; Requests does not expose a separate
  pool-timeout exception. Requests streaming is observed through `iter_content()`
  (also used by `content` and `iter_lines()`) and `close()`, not direct `raw` reads.
- Counters retain no URLs, headers, bodies or exception messages. No new metric
  labels are added. HTTP totals sum concurrent calls and may exceed response time.

For example, an application that catches a downstream read timeout can still
return 200 while logging `http: {"count": 1, "ms": 50.2,
"errors": {"read_timeout": 1}}`.

Without these helpers, other instrumented clients continue to contribute sampled
client spans to the totals when tracing is enabled.

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

