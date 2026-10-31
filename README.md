# fastapi-observer

[![CI](https://github.com/Vitaee/FastapiObserver/actions/workflows/ci.yml/badge.svg)](https://github.com/Vitaee/FastapiObserver/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/fastapi-observer)](https://pypi.org/project/fastapi-observer/)
[![Python](https://img.shields.io/pypi/pyversions/fastapi-observer)](https://pypi.org/project/fastapi-observer/)
[![License](https://img.shields.io/pypi/l/fastapi-observer)](https://github.com/Vitaee/FastapiObserver/blob/main/LICENSE)

**One-call observability for FastAPI.** JSON logs with request IDs, Prometheus metrics and OpenTelemetry traces, with sensitive data redacted by default. No vendor backend: send it to whatever you already run.

```python
from fastapi import FastAPI
from fastapiobserver import install_observability

app = FastAPI()
install_observability(app)
```

Every request now writes one JSON line, with the same `trace_id` as its trace when tracing is on:

```json
{
  "timestamp": "2026-10-31T09:10:20.345831+00:00",
  "level": "INFO",
  "logger": "fastapiobserver.middleware",
  "message": "request.completed",
  "app_name": "orders-api",
  "service": "orders",
  "environment": "production",
  "version": "2.3.1",
  "request_id": "d496f541-5e5e-4f14-9174-e9bd697faa62",
  "trace_id": "11d5b6f088bfdba501927e55463be4cd",
  "span_id": "50a12c2d9937eacb",
  "event": {
    "method": "GET",
    "path": "/orders/42",
    "status_code": 200,
    "duration_ms": 1.314,
    "client_ip": "203.0.113.7",
    "error_type": "ok"
  }
}
```

**Documentation:** [vitaee.github.io/FastapiObserver](https://vitaee.github.io/FastapiObserver/)

## Install

```bash
pip install fastapi-observer                 # JSON logs, request IDs, redaction
pip install "fastapi-observer[prometheus]"   # + Prometheus /metrics
pip install "fastapi-observer[otel]"         # + OpenTelemetry traces and OTLP logs
pip install "fastapi-observer[all]"          # everything
```

Requires Python 3.11 or newer.

## Configure with environment variables

`install_observability(app)` reads its settings from the environment. These are the ones most apps set:

| Variable | Example | What it does |
|---|---|---|
| `APP_NAME`, `SERVICE_NAME`, `ENVIRONMENT`, `APP_VERSION` | `orders-api`, `orders`, `production`, `2.3.1` | Added to every log line |
| `METRICS_ENABLED` | `true` | Serves Prometheus metrics at `/metrics` (needs `[prometheus]`) |
| `OTEL_ENABLED`, `OTEL_EXPORTER_OTLP_ENDPOINT` | `true`, `http://collector:4317` | Sends traces over OTLP and adds `trace_id` to logs (needs `[otel]`) |
| `OBS_REDACTION_PRESET` | `strict`, `pci`, `gdpr` | Chooses which fields and headers are masked |
| `OBS_PROFILE` | `development`, `production` | Sensible defaults: `production` sets `INFO` logs, a larger log queue and the `strict` preset |

Explicit variables and Python arguments always win over profile defaults. Every setting is listed in the [configuration guide](https://vitaee.github.io/FastapiObserver/configuration/).

## What you get

- **One JSON line per request** with method, path, status, duration and client IP, and your service's name, environment and version on every line, including your own `logging` calls.
- **Request IDs** taken from `X-Request-ID` when the caller is a trusted proxy, generated otherwise, and returned in the response.
- **Redaction on by default.** Passwords, tokens, API keys, `Authorization` and cookies are masked before anything is written. Presets cover stricter needs (`strict`, `pci`, `gdpr`).
- **Prometheus metrics** labelled by route template (`/orders/{order_id}`), so URLs with IDs don't explode cardinality.
- **OpenTelemetry traces** with `trace_id` and `span_id` in every log line, and optional log export over OTLP.
- **Non-blocking log writes.** Lines are written from a background thread, so a slow stdout never stalls the event loop.

Optional extras: runtime log-level control, a tamper-evident audit log, SQLAlchemy, `httpx` and `requests` tracing, Strawberry GraphQL and a Loguru bridge. See the [documentation](https://vitaee.github.io/FastapiObserver/).

![Grafana dashboard built from fastapi-observer metrics, traces and logs](https://raw.githubusercontent.com/Vitaee/FastapiObserver/main/examples/full_stack/screenshot/dashboard_top.png)

*The Grafana dashboard from [`examples/full_stack`](https://github.com/Vitaee/FastapiObserver/tree/main/examples/full_stack), a Docker Compose stack you can run locally.*

## How it compares

| | fastapi-observer | structlog or loguru | OpenTelemetry auto-instrumentation | Logfire |
|---|---|---|---|---|
| JSON log line per request | Built in | You write the middleware | No | A span per request |
| Request ID on every log line | Built in | You wire it up | No | Trace IDs |
| Redaction by default | Yes, with presets | You write a processor | No | Yes |
| Prometheus `/metrics` | Built in | No | Through an exporter | No |
| Traces | OpenTelemetry | No | Yes | Yes |
| Where data goes | stdout, Prometheus and any OTLP backend | stdout or files | Any OTLP backend | Logfire's platform |

**When not to use it:**

- You only want logs and need the lowest possible overhead: structlog alone does less work per line.
- You already send everything to Logfire, Datadog or Sentry: their own FastAPI integrations fit better.
- Your app isn't built on FastAPI or Starlette.

What it costs: with default settings, about 43 µs of CPU per request on an Apple M1. See the [benchmarks](https://vitaee.github.io/FastapiObserver/benchmarks/), including a comparison with wiring the same stack by hand.

## Works with

- **Servers:** uvicorn, gunicorn with `UvicornWorker`, and Granian. Uvicorn's duplicate access line is dropped automatically. To get the servers' own startup lines as JSON too, see [running with gunicorn and Granian](https://vitaee.github.io/FastapiObserver/servers/).
- **Backends:** Prometheus, Grafana (Loki, Tempo), Jaeger and anything that accepts OTLP. See [backends](https://vitaee.github.io/FastapiObserver/backends/).
- **Error tracking:** runs alongside Sentry. See the [Sentry notes](https://vitaee.github.io/FastapiObserver/sentry/).

## Why you can trust it

- Tested in CI on Python 3.11 to 3.15, type-checked with `mypy --strict`, tested nightly against the newest OpenTelemetry releases, and used in production by its maintainer.
- Releases are published with PyPI Trusted Publishing and signed attestations. CI runs `pip-audit` and generates an SBOM.
- Security fixes are called out in the [changelog](https://github.com/Vitaee/FastapiObserver/blob/main/CHANGELOG.md). To report a vulnerability, see [SECURITY.md](https://github.com/Vitaee/FastapiObserver/blob/main/SECURITY.md).
- Benchmarks are reproducible from [`examples/benchmarks`](https://github.com/Vitaee/FastapiObserver/tree/main/examples/benchmarks).

## Compatibility

| Component | Supported |
|---|---|
| Python | `>=3.11` (CI: `3.11` to `3.15`) |
| FastAPI | `>=0.133.0` |
| Starlette | `>=1.3.1` |
| Prometheus extra | `prometheus-client>=0.24.1` |
| OpenTelemetry extra | `opentelemetry-api/sdk/exporter>=1.45.1` |

## Documentation

- [Configuration](https://vitaee.github.io/FastapiObserver/configuration/) and [structured logging](https://vitaee.github.io/FastapiObserver/logging/)
- [Security and redaction presets](https://vitaee.github.io/FastapiObserver/security/)
- [OpenTelemetry](https://vitaee.github.io/FastapiObserver/opentelemetry/)
- [Production deployment](https://vitaee.github.io/FastapiObserver/deployment/) and [Kubernetes](https://vitaee.github.io/FastapiObserver/kubernetes/)
- [Benchmarks](https://vitaee.github.io/FastapiObserver/benchmarks/) and [performance tuning](https://vitaee.github.io/FastapiObserver/tuning/)
- [Contributing](https://vitaee.github.io/FastapiObserver/contributing/)

## Support the project

If this library saves you time, you can [buy me a coffee](https://buymeacoffee.com/FYbPCSu).
