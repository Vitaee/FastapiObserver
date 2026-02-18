# fastapi-observer

**Production-grade observability for FastAPI in one function call.**

Structured JSON logs, request correlation, Prometheus metrics, OpenTelemetry tracing, security redaction, and runtime controls — all wired up automatically.

**Supported Python versions:** `3.10` to `3.14`

---

## What This Package Does (The Big Picture)

When you build a FastAPI app, you typically need to set up:
- Structured logging (not just `print()` or basic `logging.info()`)
- Request tracing (unique ID per request, shared across services)
- Metrics (request counts, latencies for Grafana dashboards)
- Security (don't log passwords, tokens, or credit card numbers)
- OpenTelemetry (distributed tracing across microservices)

Each of these requires 50-200 lines of glue code. `fastapi-observer` replaces all of that with **6 lines**:

```python
from fastapiobserver import ObservabilitySettings, install_observability

app = FastAPI()
settings = ObservabilitySettings(app_name="my-api", service="orders", environment="production")
install_observability(app, settings)
# Done. Every request is now logged, metricked, and secured by default.
```

---

## Install

```bash
# Core (logging + metrics + security)
pip install fastapi-observer

# With Prometheus metrics
pip install "fastapi-observer[prometheus]"

# With OpenTelemetry tracing
pip install "fastapi-observer[otel]"

# Everything
pip install "fastapi-observer[all]"
```

Import path:
```python
import fastapiobserver
```

---

## Quick Start

```python
from fastapi import FastAPI
from fastapiobserver import (
    ObservabilitySettings,
    SecurityPolicy,
    TrustedProxyPolicy,
    install_observability,
)

app = FastAPI()

settings = ObservabilitySettings(
    app_name="orders-api",
    service="orders",
    environment="production",
    version="0.1.0",
    metrics_enabled=True,
)

install_observability(
    app,
    settings,
    security_policy=SecurityPolicy(),
    trusted_proxy_policy=TrustedProxyPolicy(enabled=True),
)
```

### What happens when you call `install_observability()`

```
install_observability(app, settings)
        │
        ├── 1. setup_logging()
        │       • Creates a JSON formatter for all log output
        │       • Sets up QueueHandler → QueueListener pipeline
        │         (logging I/O happens in a background thread,
        │          so your request handler is never blocked)
        │       • Redirects uvicorn's own loggers to use the same format
        │
        ├── 2. build_metrics_backend()
        │       • Creates a Prometheus backend (counters + histogram)
        │       • Mounts GET /metrics endpoint on your app
        │       • Labels every metric with service + environment
        │
        ├── 3. install_otel()  (if otel_settings provided)
        │       • Detects if another library already set up a TracerProvider
        │       • If not: creates TracerProvider + OTLP exporter + BatchSpanProcessor
        │       • Instruments FastAPI (auto-spans for every request)
        │       • Patches Python logging (trace_id injected into every log)
        │
        ├── 4. Middleware ordering check
        │       • If body capture is on AND other middleware exists,
        │         logs a warning ("install observability first")
        │
        ├── 5. Add RequestLoggingMiddleware
        │       • Wraps every HTTP request/response
        │       • Generates or trusts incoming x-request-id
        │       • Starts a performance timer
        │       • On completion: builds a sanitized event dict, logs it,
        │         records metrics, cleans up context
        │
        └── 6. mount_control_plane()  (if runtime_control_settings provided)
                • Adds POST /_observability/control endpoint
                • Allows changing log level + trace sampling at runtime
```

### What a single request looks like

When a request hits your app, here's the exact sequence:

```
Incoming HTTP Request
        │
        ▼
RequestLoggingMiddleware.__call__()
        │
        ├── Decode headers from raw bytes
        ├── Resolve client IP (respecting trusted proxies)
        ├── Generate or trust x-request-id
        ├── Parse traceparent header → trace_id, span_id
        ├── Store request_id, trace_id, span_id in ContextVars
        │   (accessible anywhere via get_request_id(), get_trace_id(), etc.)
        ├── Check if body capture is allowed (media-type check)
        ├── Start perf_counter timer
        │
        ├── Pass request to YOUR app code ──────────────────┐
        │                                                    │
        │   @app.get("/orders/{id}")                        │
        │   def get_order(id):                              │
        │       logger.info("fetching order")  ← trace_id  │
        │       return {"order_id": id}      auto-injected  │
        │                                                    │
        ├── ◄────────── response comes back ────────────────┘
        │
        ├── Classify error: ok / client_error / server_error / unhandled_exception
        ├── Build event dict: {method, path, status_code, duration_ms, client_ip, ...}
        ├── Sanitize: apply redaction rules (mask/hash/drop sensitive fields)
        ├── Sanitize: apply allowlists (drop unlisted headers/keys)
        ├── Log the event (INFO for 2xx, WARNING for 4xx, ERROR for 5xx)
        ├── Record Prometheus metrics (histogram + counter)
        ├── Emit plugin metric hooks
        └── Clear ContextVars (request_id, trace_id, span_id)
```

**Example JSON log output:**

```json
{
  "timestamp": "2026-02-18T10:30:00.000000+00:00",
  "level": "INFO",
  "logger": "fastapiobserver.middleware",
  "message": "request.completed",
  "app_name": "orders-api",
  "service": "orders",
  "environment": "production",
  "version": "0.1.0",
  "log_schema_version": "1.0.0",
  "library": "fastapiobserver",
  "request_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "trace_id": "0af7651916cd43dd8448eb211c80319c",
  "span_id": "b7ad6b7169203331",
  "event": {
    "method": "GET",
    "path": "/orders/42",
    "status_code": 200,
    "duration_ms": 3.456,
    "client_ip": "10.0.0.1",
    "error_type": "ok"
  }
}
```

---

## Security Defaults

Out of the box, the package protects you from common logging mistakes:

| Protection | Default | Why |
|---|---|---|
| Body logging | **OFF** | Prevents accidentally logging passwords in POST bodies |
| Sensitive field masking | **ON** | `password`, `token`, `secret`, etc. → `"***"` |
| Header masking | **ON** | `authorization`, `cookie`, `x-api-key` → `"***"` |
| Query string in path | **Excluded** | `/search?token=abc` logs as `/search` |
| Request ID trust | **Trusted CIDRs only** | Prevents clients from spoofing `x-request-id` |

### Security Presets

For regulated environments, use a preset instead of configuring individual fields:

```python
# STRICT — Drop sensitive fields entirely, only allow 4 safe headers
policy = SecurityPolicy.from_preset("strict")

# PCI — Mask cardholder data (card_number, cvv, pan, expiry, track_data)
policy = SecurityPolicy.from_preset("pci")

# GDPR — Hash PII with SHA-256 (email, phone, address, date_of_birth, ssn, ...)
policy = SecurityPolicy.from_preset("gdpr")
```

**How presets work under the hood:**

```python
# Each preset is a dict of SecurityPolicy overrides:
SECURITY_POLICY_PRESETS = {
    "strict": {
        "redaction_mode": "drop",          # Remove sensitive keys entirely
        "log_request_body": False,
        "log_response_body": False,
        "header_allowlist": ("x-request-id", "traceparent", "user-agent", "content-type"),
    },
    "pci": {
        "redaction_mode": "mask",          # Replace values with "***"
        "redacted_fields": DEFAULT_FIELDS + ("card-number", "cvv", "pan", "expiry", "track-data"),
        ...
    },
    "gdpr": {
        "redaction_mode": "hash",          # Replace with "sha256:<hex>"
        "redacted_fields": DEFAULT_FIELDS + ("email", "phone", "address", "date-of-birth", "ssn", ...),
        ...
    },
}

# from_preset() creates a SecurityPolicy from these overrides.
# You can layer your own overrides on top:
policy = SecurityPolicy.from_preset("gdpr").model_copy(update={"redaction_mode": "mask"})
```

**Via environment variable:**

```bash
export OBS_REDACTION_PRESET=gdpr
export OBS_REDACTION_MODE=mask  # Override the preset's hash mode
export OBS_HEADER_ALLOWLIST=none  # Explicitly unset a preset allowlist
```

```python
policy = SecurityPolicy.from_env()  # Loads preset first, then applies env overrides
```

`OBS_HEADER_ALLOWLIST`, `OBS_EVENT_KEY_ALLOWLIST`, and `OBS_BODY_CAPTURE_MEDIA_TYPES` accept `none`, `null`, or `unset` to clear preset values.

Preset constants are part of the public API:

```python
from fastapiobserver import (
    SECURITY_POLICY_PRESETS,
    PCI_REDACTED_FIELDS,
    GDPR_REDACTED_FIELDS,
    STRICT_HEADER_ALLOWLIST,
)
```

### Allowlist-Only Logging

Instead of blocking known-bad keys (blocklist), you can allow ONLY specific keys (allowlist):

```python
policy = SecurityPolicy(
    # Only these headers appear in logs — everything else is dropped:
    header_allowlist=("x-request-id", "content-type", "user-agent"),
    # Only these event keys appear — client_ip, duration_ms, etc. are dropped:
    event_key_allowlist=("method", "path", "status_code"),
)
```

**Why use allowlists?** In SOC 2, HIPAA, and PCI-DSS audits, you need to prove that ONLY specific data is logged. A blocklist can miss new fields added in future versions; an allowlist guarantees nothing unexpected leaks.

### Body Capture Media-Type Allowlist

When body logging is enabled, you can restrict which content types are captured:

```python
policy = SecurityPolicy(
    log_request_body=True,
    # Only capture JSON bodies — binary uploads, multipart, etc. are skipped:
    body_capture_media_types=("application/json",),
)
```

**Under the hood:** The middleware checks the `Content-Type` header before capturing. For response bodies, the check is deferred until `http.response.start` (because the response content type isn't known at the start of the request).

---

## Error Classification

Every request log includes an `error_type` field and uses appropriate log levels:

| Scenario | `error_type` | Log Level |
|---|---|---|
| Normal 2xx response | `ok` | `INFO` |
| 4xx from a route | `client_error` | `WARNING` |
| 5xx from a route | `server_error` | `ERROR` |
| `HTTPException(4xx)` raised | `client_error_exception` | `WARNING` |
| `HTTPException(5xx)` raised | `server_error_exception` | `ERROR` |
| Unhandled `RuntimeError`, etc. | `unhandled_exception` | `ERROR` (with traceback) |

This lets you set up Grafana alerts on `error_type = "unhandled_exception"` without parsing status codes.

---

## OpenTelemetry Tracing

```python
from fastapiobserver.otel import OTelSettings

otel_settings = OTelSettings(
    enabled=True,
    service_name="orders-api",
    service_version="2.0.0",
    environment="production",
    otlp_endpoint="http://localhost:4317",     # Your OTLP collector
    protocol="grpc",                           # "grpc" or "http/protobuf"
    trace_sampling_ratio=1.0,                  # 1.0 = 100%, 0.1 = 10%
    extra_resource_attributes={                # Custom span metadata
        "k8s.namespace": "prod",
        "team": "backend",
    },
)

install_observability(app, settings, otel_settings=otel_settings)
```

**How OTel integration works under the hood:**

1. **External provider detection** — If another library (like `opentelemetry-instrument`) already configured a `TracerProvider`, we reuse it instead of creating a duplicate. This prevents double-tracing.

2. **Dynamic sampling** — The `DynamicTraceIdRatioSampler` reads the sampling ratio from a thread-safe global variable. You can change it at runtime via the control plane without restarting.

3. **Log correlation** — The `TraceContextFilter` reads the current OTel span on every log record and injects `trace_id` and `span_id`. This means clicking a log line in Grafana/Loki can jump straight to the trace in Jaeger/Tempo.

4. **Resource attributes** — `extra_resource_attributes` are merged into the OTel resource, appearing on every span. Set via code or env:
   ```bash
   export OTEL_EXTRA_RESOURCE_ATTRIBUTES="k8s.namespace=prod,team=backend"
   ```

---

## Runtime Control Plane

Change log level and trace sampling ratio without restarting the server:

```bash
export OBSERVABILITY_CONTROL_TOKEN="replace-me"
```

```python
from fastapiobserver import RuntimeControlSettings, mount_control_plane

mount_control_plane(app, RuntimeControlSettings(enabled=True))
```

```bash
curl -X POST http://localhost:8000/_observability/control \
  -H "Authorization: Bearer replace-me" \
  -H "Content-Type: application/json" \
  -d '{"log_level":"DEBUG","trace_sampling_ratio":0.25}'
```

**Under the hood:** The control plane endpoint validates the bearer token, then calls `logging.getLogger().setLevel()` for log level changes and `set_trace_sampling_ratio()` for sampling changes. The `DynamicTraceIdRatioSampler` picks up the new ratio immediately — no restart needed.

---

## OTel Test Coverage

The repository includes integration-oriented OTel tests:

- `tests/test_otel_log_correlation.py` verifies `trace_id`/`span_id` in logs match real spans from an instrumented request.
- `tests/test_otlp_export_integration.py` validates OTLP HTTP export using a local collector fixture in `tests/conftest_otlp.py`.

Note: in restricted environments where opening local sockets is blocked, the OTLP collector test is skipped automatically.

---

## Plugin Hooks

Extend logging and metrics without modifying the core:

```python
from fastapiobserver import register_log_enricher, register_metric_hook

# Add custom fields to every log line:
def add_git_sha(payload: dict) -> dict:
    payload["git_sha"] = "abc123"
    return payload
register_log_enricher("git_sha", add_git_sha)

# Run custom logic after each request's metrics are recorded:
def track_slow_requests(request, response, duration):
    if duration > 1.0:
        print(f"SLOW: {request.url.path} took {duration:.2f}s")
register_metric_hook("slow_requests", track_slow_requests)
```

**Plugin isolation:** If your enricher or hook raises an exception, it's caught and logged — it never crashes the request. Other plugins continue to function normally.

---

## Middleware Ordering (Body Capture)

When body capture is enabled, install observability **before** any other middleware:

```python
#  Correct — observability is outermost, sees raw request/response
install_observability(app, settings, security_policy=SecurityPolicy(log_request_body=True))
app.add_middleware(CORSMiddleware, ...)

#  Warning — CORS might consume the body before observability sees it
app.add_middleware(CORSMiddleware, ...)
install_observability(app, settings, security_policy=SecurityPolicy(log_request_body=True))
```

The package will log a warning if other middleware is already registered when body capture is active.

---

## Multi-Worker Gunicorn (Prometheus)

For Gunicorn with multiple Uvicorn workers:

```bash
export PROMETHEUS_MULTIPROC_DIR=/tmp/prometheus-metrics
rm -rf "$PROMETHEUS_MULTIPROC_DIR"
mkdir -p "$PROMETHEUS_MULTIPROC_DIR"
```

In `gunicorn.conf.py`:
```python
from fastapiobserver import mark_prometheus_process_dead

def child_exit(server, worker):
    mark_prometheus_process_dead(worker.pid)
```

**Under the hood:** Prometheus client library uses per-process files in `PROMETHEUS_MULTIPROC_DIR`. When a worker dies, `mark_prometheus_process_dead()` cleans up its metric files so they don't pollute the aggregated `/metrics` output.

---

## Example Apps

The `examples/` directory includes runnable demos:

| Example | What It Shows |
|---|---|
| [`basic_app.py`](examples/basic_app.py) | Minimal setup, request ID propagation |
| [`security_presets_app.py`](examples/security_presets_app.py) | PCI preset, body capture, media-type allowlist |
| [`otel_app.py`](examples/otel_app.py) | OpenTelemetry tracing, resource attributes, log correlation |
| [`allowlist_app.py`](examples/allowlist_app.py) | Allowlist-only sanitization for compliance |

Run any example:
```bash
uvicorn examples.basic_app:app --reload
```

---

## Centralized Monitoring Across Multiple Servers

Recommended topology:
- Every FastAPI instance exposes `/metrics` and OTLP traces/logs.
- A central Prometheus scrapes all instances.
- Grafana reads from central Prometheus/Loki/Tempo.

Prometheus target labels (`job`, `instance`) plus this package's metric labels (`service`, `environment`) make cross-server dashboards and filtering straightforward.

---

## Environment Variables Reference

All settings can be configured via environment variables:

| Variable | Default | Description |
|---|---|---|
| `OBS_REDACTION_PRESET` | — | Load a preset: `strict`, `pci`, `gdpr` |
| `OBS_REDACTED_FIELDS` | _(default list)_ | CSV of field names to redact |
| `OBS_REDACTED_HEADERS` | _(default list)_ | CSV of header names to redact |
| `OBS_REDACTION_MODE` | `mask` | `mask`, `hash`, or `drop` |
| `OBS_MASK_TEXT` | `***` | Text used for masking |
| `OBS_LOG_REQUEST_BODY` | `false` | Enable request body logging |
| `OBS_LOG_RESPONSE_BODY` | `false` | Enable response body logging |
| `OBS_MAX_BODY_LENGTH` | `256` | Max body bytes to capture |
| `OBS_HEADER_ALLOWLIST` | — | CSV of allowed header names |
| `OBS_EVENT_KEY_ALLOWLIST` | — | CSV of allowed event key names |
| `OBS_BODY_CAPTURE_MEDIA_TYPES` | — | CSV of allowed content types |
| `OBS_TRUSTED_PROXY_ENABLED` | `true` | Enable trust boundary |
| `OBS_TRUSTED_CIDRS` | _(RFC 1918)_ | CSV of trusted CIDR ranges |
| `OBS_HONOR_FORWARDED_HEADERS` | `false` | Trust `X-Forwarded-For` |
| `OTEL_ENABLED` | `false` | Enable OpenTelemetry |
| `OTEL_SERVICE_NAME` | — | Override service name for traces |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | — | OTLP collector endpoint |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | `grpc` | `grpc` or `http/protobuf` |
| `OTEL_TRACE_SAMPLING_RATIO` | `1.0` | Initial sampling ratio (0.0–1.0) |
| `OTEL_EXTRA_RESOURCE_ATTRIBUTES` | — | CSV of `key=value` pairs |
| `OBSERVABILITY_CONTROL_TOKEN` | — | Bearer token for control plane |

---

## Release Tracks

- `0.1.x`: secure-by-default core
- `0.2.x`: OpenTelemetry-native interoperability, security presets, allowlists
- `1.0.0`: dynamic runtime controls and plugin stability

Current development version: `0.2.0.dev1`

## Changelog Policy

Any breaking change must be called out under a `Breaking Changes` section in `CHANGELOG.md`.

---

## Packaging and Publishing

### 1) Build distributions

```bash
python -m pip install --upgrade pip build
python -m build
```

### 2) Upload to TestPyPI

```bash
python -m pip install --upgrade twine
python -m twine upload --repository testpypi dist/*
```

### 3) Validate install from TestPyPI

```bash
python -m pip install \
  --extra-index-url https://test.pypi.org/simple/ \
  fastapi-observer
```

### 4) Upload to production PyPI

```bash
python -m twine upload dist/*
```

The repository also includes GitHub Actions workflows for CI, SBOM generation, and Trusted Publishing.

---

## Roadmap Tracking

See [NEXT_STEPS.md](NEXT_STEPS.md) for the active `0.2.0` roadmap and release checklist.
