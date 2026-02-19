# fastapi-observer

**Zero-glue observability for FastAPI.**

`fastapi-observer` gives you structured JSON logs, request correlation, Prometheus metrics, OpenTelemetry tracing, security redaction presets, and runtime controls in one install step and one function call.

**Supported Python versions:** `3.10` to `3.14`

---

## Why This Package Exists

Most FastAPI services eventually need the same observability plumbing:
- Structured JSON logging
- Request and trace correlation
- Metrics for dashboards and alerts
- OpenTelemetry setup
- Redaction/sanitization for sensitive data
- Runtime controls for incident response

Teams usually implement this as custom glue code in every service. That costs engineering time and creates drift between services.

`fastapi-observer` replaces this repeated wiring with a consistent, secure-by-default setup.

---

## Read This by Role

If you are a new graduate or new to observability:
1. Start with [Install](#install)
2. Run [5-Minute Quick Start](#5-minute-quick-start)
3. Read [Security Defaults and Presets](#security-defaults-and-presets)
4. Copy an app from [Examples](#examples)

If you are a senior engineer, architect, or CTO:
1. Read [What You Get Immediately](#what-you-get-immediately)
2. Review [Runtime Control Plane (No Restart)](#runtime-control-plane-no-restart)
3. Review [OpenTelemetry (Traces + Optional OTLP Logs)](#opentelemetry-traces--optional-otlp-logs)
4. Use [Production Deployment Pattern](#production-deployment-pattern)

---

## What You Get Immediately

After one call to `install_observability()`:

| Capability | Included | Default |
|---|---|---|
| Structured JSON logs | Yes | Enabled |
| Request ID correlation | Yes | Enabled |
| Trace/span IDs in logs | Yes (with OTel) | Off until OTel enabled |
| Prometheus `/metrics` | Yes | Off until `metrics_enabled=True` |
| Sensitive-data redaction | Yes | Enabled |
| Security presets (`strict`, `pci`, `gdpr`) | Yes | Available |
| Runtime control endpoint | Yes | Off until enabled |
| Plugin hooks for enrichment/hooks | Yes | Available |

---

## Install

```bash
# Core (logging + metrics + security)
pip install fastapi-observer

# Prometheus metrics support
pip install "fastapi-observer[prometheus]"

# OpenTelemetry tracing/logs support
pip install "fastapi-observer[otel]"

# Everything
pip install "fastapi-observer[all]"
```

Import path:

```python
import fastapiobserver
```

---

## 5-Minute Quick Start

```python
from fastapi import FastAPI
from fastapiobserver import ObservabilitySettings, install_observability

app = FastAPI()

settings = ObservabilitySettings(
    app_name="orders-api",
    service="orders",
    environment="production",
    version="0.1.0",
    metrics_enabled=True,
)

install_observability(app, settings)


@app.get("/orders/{order_id}")
def get_order(order_id: int) -> dict[str, int]:
    return {"order_id": order_id}
```

Run:

```bash
uvicorn main:app --reload
```

Now you have:
- Structured request logs on every request
- Request ID propagation
- Sanitized event payloads
- Prometheus metrics at `/metrics`

---

## Security Defaults and Presets

### Default protections

| Protection | Default | Why |
|---|---|---|
| Body logging | `OFF` | Avoid leaking request/response secrets |
| Sensitive key masking | `ON` | Protect fields like `password`, `token`, `secret` |
| Sensitive header masking | `ON` | Protect `authorization`, `cookie`, `x-api-key` |
| Query string in logged path | Excluded | Prevent accidental token leakage |
| Request ID trust boundary | Trusted CIDRs only | Prevent spoofed correlation IDs |

### Presets for regulated environments

```python
from fastapiobserver import SecurityPolicy

# Strictest option: drop sensitive values and keep minimal safe headers
strict_policy = SecurityPolicy.from_preset("strict")

# PCI-focused redaction fields
pci_policy = SecurityPolicy.from_preset("pci")

# GDPR-focused hashed PII fields
gdpr_policy = SecurityPolicy.from_preset("gdpr")
```

Use a preset in installation:

```python
install_observability(app, settings, security_policy=SecurityPolicy.from_preset("pci"))
```

### Allowlist-only logging (audit-style)

If your compliance model is "log only approved fields", use allowlists:

```python
from fastapiobserver import SecurityPolicy

policy = SecurityPolicy(
    header_allowlist=("x-request-id", "content-type", "user-agent"),
    event_key_allowlist=("method", "path", "status_code"),
)
```

### Body capture media-type guard

```python
policy = SecurityPolicy(
    log_request_body=True,
    body_capture_media_types=("application/json",),
)
```

---

## Runtime Control Plane (No Restart)

Use runtime controls when you need higher log verbosity or different trace sampling during an incident.

```bash
export OBSERVABILITY_CONTROL_TOKEN="replace-me"
```

```python
from fastapiobserver import RuntimeControlSettings, install_observability

runtime_control = RuntimeControlSettings(enabled=True)
install_observability(app, settings, runtime_control_settings=runtime_control)
```

Inspect current runtime values:

```bash
curl -X GET http://localhost:8000/_observability/control \
  -H "Authorization: Bearer replace-me"
```

Update runtime values:

```bash
curl -X POST http://localhost:8000/_observability/control \
  -H "Authorization: Bearer replace-me" \
  -H "Content-Type: application/json" \
  -d '{"log_level":"DEBUG","trace_sampling_ratio":0.25}'
```

What changes immediately:
- Root logger level (and uvicorn loggers)
- Dynamic OTel trace sampling ratio

---

## OpenTelemetry (Traces + Optional OTLP Logs)

```python
from fastapiobserver import OTelLogsSettings, OTelSettings, install_observability

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

install_observability(
    app,
    settings,
    otel_settings=otel_settings,
    otel_logs_settings=otel_logs_settings,
)
```

Design details:
- Reuses an externally configured tracer provider if one already exists.
- Injects trace IDs into application logs for log-trace correlation.
- Supports runtime sampling updates through the control plane.
- Sends OTel logs in OTLP mode with the same sanitization policy.

---

## What `install_observability()` Wires Up

1. Structured logging pipeline (JSON formatter + async queue handler).
2. Metrics backend and `/metrics` endpoint when metrics are enabled.
3. OTel tracing setup when OTel is enabled.
4. Request logging middleware with sanitization and context cleanup.
5. Runtime control endpoint when runtime control is enabled.

Request path lifecycle (high-level):

```text
Request arrives
  -> request ID / trace context resolved
  -> app handler executes
  -> response classified (ok/client_error/server_error/exception)
  -> payload sanitized by policy
  -> log emitted + metrics recorded
  -> context cleared
```

---

## Example JSON Log Event

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

## Production Deployment Pattern

Recommended topology:
- FastAPI services emit logs/metrics/traces.
- Prometheus scrapes each service's `/metrics`.
- Traces/logs are shipped through an OpenTelemetry Collector (or Alloy).
- Grafana/Tempo/Loki read from centralized backends.

Minimal collector reference:

```yaml
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
      http:
        endpoint: 0.0.0.0:4318

processors:
  memory_limiter:
    limit_mib: 512
    spike_limit_mib: 128
    check_interval: 5s
  batch:
    send_batch_size: 512
    timeout: 5s

exporters:
  otlp/tempo:
    endpoint: tempo:4317
  otlp/loki:
    endpoint: loki:3100

service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: [memory_limiter, batch]
      exporters: [otlp/tempo]
    logs:
      receivers: [otlp]
      processors: [memory_limiter, batch]
      exporters: [otlp/loki]
```

Operational notes for leadership:
- Use TLS and authenticated exporters in production.
- Keep high-fidelity traces for errors and slow paths; sample the rest.
- Keep one policy for sanitization across local and OTLP sinks.

---

## Examples

The `examples/` directory contains runnable demos:

| Example | What it shows |
|---|---|
| [`basic_app.py`](examples/basic_app.py) | Minimal setup and request logging |
| [`security_presets_app.py`](examples/security_presets_app.py) | Preset-based security policy |
| [`allowlist_app.py`](examples/allowlist_app.py) | Allowlist-only sanitization |
| [`otel_app.py`](examples/otel_app.py) | OTel tracing and resource attributes |
| [`full_stack/`](examples/full_stack/) | **Docker Compose stack**: 3 FastAPI services + Grafana + Prometheus + Loki + Tempo |

Run an example:

```bash
uvicorn examples.basic_app:app --reload
```

### Dashboard Screenshots (Full-Stack Demo)

From `examples/full_stack`, these are real Grafana views generated by `fastapi-observer` telemetry:

**Overview panels (latency heatmap, route throughput, errors, CPU/memory):**

![FastAPI Observer dashboard overview](examples/full_stack/screenshot/dashboard_top.png)

**Percentiles, request rate, and structured JSON logs in Loki:**

![FastAPI Observer dashboard logs and percentiles](examples/full_stack/screenshot/dashboard_bottom.png)

---

## Environment Variables

The library supports configuration from code and env vars. Below are the most relevant env vars by area.

### Identity and logging

| Variable | Default | Description |
|---|---|---|
| `APP_NAME` | `app` | Namespace for app-level identity |
| `SERVICE_NAME` | `api` | Service label for logs/metrics |
| `ENVIRONMENT` | `development` | Environment label |
| `APP_VERSION` | `0.0.0` | Service version |
| `LOG_LEVEL` | `INFO` | Root log level |
| `LOG_DIR` | - | Optional file log directory |
| `REQUEST_ID_HEADER` | `x-request-id` | Incoming request ID header |
| `RESPONSE_REQUEST_ID_HEADER` | `x-request-id` | Response request ID header |

### Metrics

| Variable | Default | Description |
|---|---|---|
| `METRICS_ENABLED` | `false` | Enable metrics backend |
| `METRICS_PATH` | `/metrics` | Metrics endpoint path |
| `METRICS_EXCLUDE_PATHS` | `/metrics,/health,/healthz,/docs,/openapi.json` | Skip metrics for noisy endpoints |
| `METRICS_EXEMPLARS_ENABLED` | `false` | Enable exemplars where supported |
| `METRICS_FORMAT` | `negotiate` | `prometheus`, `openmetrics`, or `negotiate` |

### Security and trust boundary

| Variable | Default | Description |
|---|---|---|
| `OBS_REDACTION_PRESET` | - | `strict`, `pci`, `gdpr` |
| `OBS_REDACTED_FIELDS` | built-in list | CSV keys to redact |
| `OBS_REDACTED_HEADERS` | built-in list | CSV headers to redact |
| `OBS_REDACTION_MODE` | `mask` | `mask`, `hash`, `drop` |
| `OBS_MASK_TEXT` | `***` | Mask replacement text |
| `OBS_LOG_REQUEST_BODY` | `false` | Enable request body logging |
| `OBS_LOG_RESPONSE_BODY` | `false` | Enable response body logging |
| `OBS_MAX_BODY_LENGTH` | `256` | Max captured body bytes |
| `OBS_HEADER_ALLOWLIST` | - | CSV headers allowed in logs |
| `OBS_EVENT_KEY_ALLOWLIST` | - | CSV event keys allowed in logs |
| `OBS_BODY_CAPTURE_MEDIA_TYPES` | - | CSV allowed media types for body capture |
| `OBS_TRUSTED_PROXY_ENABLED` | `true` | Enable trusted-proxy policy |
| `OBS_TRUSTED_CIDRS` | RFC1918 + loopback | CSV trusted CIDRs |
| `OBS_HONOR_FORWARDED_HEADERS` | `false` | Trust forwarded headers |

Notes:
- `OBS_HEADER_ALLOWLIST`, `OBS_EVENT_KEY_ALLOWLIST`, and `OBS_BODY_CAPTURE_MEDIA_TYPES` accept `none`, `null`, or `unset` to clear values.

### OpenTelemetry tracing/log export

| Variable | Default | Description |
|---|---|---|
| `OTEL_ENABLED` | `false` | Enable tracing instrumentation |
| `OTEL_SERVICE_NAME` | `SERVICE_NAME` | OTel service name override |
| `OTEL_SERVICE_VERSION` | `APP_VERSION` | OTel service version override |
| `OTEL_ENVIRONMENT` | `ENVIRONMENT` | OTel environment override |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | - | OTLP endpoint |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | `grpc` | `grpc` or `http/protobuf` |
| `OTEL_TRACE_SAMPLING_RATIO` | `1.0` | Initial trace sampling ratio |
| `OTEL_EXTRA_RESOURCE_ATTRIBUTES` | - | CSV `key=value` pairs |
| `OTEL_EXCLUDED_URLS` | auto-derived | CSV excluded paths for tracing |
| `OTEL_LOGS_ENABLED` | `false` | Enable OTLP log export |
| `OTEL_LOGS_MODE` | `local_json` | `local_json`, `otlp`, `both` |
| `OTEL_LOGS_ENDPOINT` | - | OTLP logs endpoint |
| `OTEL_LOGS_PROTOCOL` | `grpc` | `grpc` or `http/protobuf` |

### Runtime control plane

| Variable | Default | Description |
|---|---|---|
| `OBS_RUNTIME_CONTROL_ENABLED` | `false` | Enable runtime control endpoint |
| `OBS_RUNTIME_CONTROL_PATH` | `/_observability/control` | Control endpoint path |
| `OBS_RUNTIME_CONTROL_TOKEN_ENV_VAR` | `OBSERVABILITY_CONTROL_TOKEN` | Name of env var containing bearer token |
| `OBSERVABILITY_CONTROL_TOKEN` | - | Bearer token value used for auth |

### Optional Logtail sink

| Variable | Default | Description |
|---|---|---|
| `LOGTAIL_ENABLED` | `false` | Enable Better Stack Logtail sink |
| `LOGTAIL_SOURCE_TOKEN` | - | Logtail source token |
| `LOGTAIL_BATCH_SIZE` | `50` | Batch size for shipping |
| `LOGTAIL_FLUSH_INTERVAL` | `2.0` | Flush interval (seconds) |

---

## Advanced Operations

### Middleware ordering for body capture

If body capture is enabled, install observability before other middleware:

```python
from fastapi.middleware.cors import CORSMiddleware
from fastapiobserver import SecurityPolicy, install_observability

install_observability(app, settings, security_policy=SecurityPolicy(log_request_body=True))
app.add_middleware(CORSMiddleware, allow_origins=["*"])
```

### Multi-worker Gunicorn with Prometheus

```bash
export PROMETHEUS_MULTIPROC_DIR=/tmp/prometheus-metrics
rm -rf "$PROMETHEUS_MULTIPROC_DIR"
mkdir -p "$PROMETHEUS_MULTIPROC_DIR"
```

`gunicorn.conf.py`:

```python
from fastapiobserver import mark_prometheus_process_dead


def child_exit(server, worker):
    mark_prometheus_process_dead(worker.pid)
```

---

## Plugin Hooks

Extend behavior without editing package internals:

```python
from fastapiobserver import register_log_enricher, register_metric_hook


def add_git_sha(payload: dict) -> dict:
    payload["git_sha"] = "abc123"
    return payload


def track_slow_requests(request, response, duration):
    if duration > 1.0:
        print(f"slow request: {request.url.path} {duration:.2f}s")


register_log_enricher("git_sha", add_git_sha)
register_metric_hook("slow_requests", track_slow_requests)
```

Plugin failures are isolated and do not crash request handling.

---

## OTel Test Coverage

Repository integration tests include:
- `tests/test_otel_log_correlation.py`: verifies trace/span IDs in logs map to real spans.
- `tests/test_otlp_export_integration.py`: validates OTLP HTTP export with local collector fixtures.

---

## Release Tracks

- `0.1.x`: secure-by-default core
- `0.2.x`: OTel interoperability, security presets, allowlists
- `1.0.0`: dynamic runtime controls and plugin stability

Current release version: `0.1.0`

## Changelog Policy

Breaking changes must be listed under a `Breaking Changes` section in `CHANGELOG.md`.

---

## Packaging and Publishing (Maintainers)

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

---

## Local Git Hook (Recommended)

```bash
git config core.hooksPath .githooks
```

The pre-push hook runs:
- `uv run ruff check`
- `uv run mypy src`
- `uv run pytest -q`

---

## Roadmap Tracking

See [NEXT_STEPS.md](NEXT_STEPS.md) for the active `0.2.0` roadmap and release checklist.
