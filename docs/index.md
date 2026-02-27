# fastapi-observer

[![CI](https://github.com/Vitaee/FastapiObserver/actions/workflows/ci.yml/badge.svg)](https://github.com/Vitaee/FastapiObserver/actions/workflows/ci.yml)
[![Sponsor](https://img.shields.io/badge/Sponsor-Buy%20me%20a%20coffee-FFDD00?logo=buymeacoffee&logoColor=000000)](https://buymeacoffee.com/FYbPCSu)

**Zero-glue observability for FastAPI.**

`fastapi-observer` gives you structured JSON logs, request correlation, Prometheus metrics, OpenTelemetry tracing, security redaction presets, and runtime controls in one install step and one function call.

**Supported Python versions:** `3.10` to `3.14`

---


## Compatibility Matrix

| Component | Supported / Tested |
|---|---|
| Python | `3.10` to `3.14` (CI matrix) |
| FastAPI | `>=0.129.0` |
| Starlette | `>=0.52.1` |
| pydantic-settings | `>=2.10.1` |
| Prometheus backend | `prometheus-client>=0.24.1` (optional extra) |
| OpenTelemetry | `opentelemetry-api/sdk/exporter>=1.39.1` (optional extra) |
| Loguru bridge | `loguru>=0.7.2` (optional extra) |

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

## Sponsor

If this library saves you engineering time, you can support maintenance here:

[buymeacoffee.com/FYbPCSu](https://buymeacoffee.com/FYbPCSu)

---

## What You Get Immediately

After one call to `install_observability()`:

| Capability | Included | Default |
|---|---|---|
| Structured JSON logs | Yes | Enabled |
| Request ID correlation | Yes | Enabled |
| Trace/span IDs in logs | Yes (with OTel) | Off until OTel enabled |
| Prometheus `/metrics` | Yes | Off until `metrics_enabled=True` |
| Native FastAPI Lifespan | Yes | Explicit opt-in via `observability_lifespan` |
| Auto-discovery | Yes | Excluded routes (`/docs`, etc.) & DB engines |
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

# Loguru coexistence bridge support
pip install "fastapi-observer[loguru]"

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

## 5-Minute Quick Start ("Zero-Glue")

You can configure the entire library via environment variables simply by calling `install_observability(app)`.

```bash
export APP_NAME="orders-api"
export SERVICE_NAME="orders"
export ENVIRONMENT="production"
export METRICS_ENABLED="true"

# Optional: Set a profile ("development" or "production") to auto-configure log levels, queues, and redaction strictness
export OBS_PROFILE="production"
```

```python
from fastapi import FastAPI
from fastapiobserver import install_observability

app = FastAPI()

# Wires up logging, metrics, OTel, and security automatically from env vars
install_observability(app)

@app.get("/orders/{order_id}")
def get_order(order_id: int) -> dict[str, int]:
    return {"order_id": order_id}

@app.get("/hidden", include_in_schema=False)
def hidden_endpoint():
    # This endpoint is automatically excluded from
    # Prometheus metrics and OTel tracing.
    return {"status": "ok"}
```

Run:

```bash
uvicorn main:app --reload
```

Now you have:
- Structured request logs on every request
- Request ID propagation
- Sanitized event payloads (enforced by the `production` profile)
- Prometheus metrics at `/metrics`

### Environment Profiles (`OBS_PROFILE`)

To dramatically reduce boilerplate, you can use the `OBS_PROFILE` environment variable to automatically set sensible defaults for your environment:

- **`OBS_PROFILE=development`**: Forces `LOG_LEVEL=DEBUG` and disables any OpenTelemetry network overhead to keep local development fast and noisy.
- **`OBS_PROFILE=production`**: Forces `LOG_LEVEL=INFO`, optimizes internal queues for massive throughput (`LOG_QUEUE_MAX_SIZE=20000`), and enforces the `strict` security redaction preset.

*(Explicit env vars or Python arguments will always override profile defaults).*

---

## Documentation Map

Use this map by goal so teams can find the right guide quickly.

### Start Here (core setup)

- [Environment Variables](configuration.md): full env-var reference for logging, metrics, security, OTel, and runtime control.
- [Security & Presets](security.md): secure defaults, redaction presets, and allowlist-first logging.
- [Structured Logging Guide](logging.md): schema contract, request event levels, and queue/sink resilience controls.
- [OpenTelemetry](opentelemetry.md): traces plus optional OTLP log/metric export.

### Feature Integrations (what you can enable)

- [GraphQL Support](graphql.md): Strawberry extension for operation-level log/trace visibility.
- [Database Tracing](db-tracing.md): SQLCommenter trace correlation for SQLAlchemy engines.
- [Plugin Hooks](plugins.md): custom enrichers, filters, metric hooks, and custom metrics backends.
- [Loguru Coexistence](loguru.md): migrate gradually without dual pipelines.
- [Logtail Sink](logtail-sink.md): Better Stack sink and DLQ durability controls.
- [Audit Logging](audit-logging.md): tamper-evident HMAC log chains for regulated environments.

### Operations and Deployment (running in production)

- [Architecture](architecture.md): what `install_observability()` wires up on request path and lifecycle.
- [Runtime Control](runtime-control.md): change log level and trace sampling without restart.
- [Production Deployment](deployment.md): rollout strategy, failure modes, alert starters, incident playbook.
- [Kubernetes Deployment](kubernetes.md): end-to-end K8s stack deployment and validation flow.
- [Performance Tuning](tuning.md): OpenTelemetry runtime tuning for high-throughput services.
- [Advanced Operations](advanced.md): Gunicorn multiprocess safety, queue controls, breaker metrics, and lifecycle behavior.

### Engineering and Maintenance

- [Benchmarks](benchmarks.md): DX reduction metrics and latency scenario matrix.
- [Maintenance & Contributing](contributing.md): test coverage, release tracks, publishing flow, and roadmap links.
