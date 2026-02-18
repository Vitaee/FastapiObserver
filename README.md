# fastapi-observer

Secure-by-default observability toolkit for FastAPI apps:
- Structured JSON logs
- Request correlation (`x-request-id`)
- Optional Prometheus metrics
- Optional OpenTelemetry tracing
- Runtime control plane with token auth
- Plugin hooks for custom enrichers and metric hooks

Supported Python versions: `3.10` to `3.14`.

## Install

Core:

```bash
pip install fastapi-observer
```

With Prometheus support:

```bash
pip install "fastapi-observer[prometheus]"
```

With OpenTelemetry support:

```bash
pip install "fastapi-observer[otel]"
```

Everything:

```bash
pip install "fastapi-observer[all]"
```

Import path remains:

```python
import fastapiobserver
```

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

## Security Defaults

- Request/response body logging disabled by default.
- Sensitive keys and headers are masked by default.
- Inbound request IDs accepted only from trusted CIDRs when trust-boundary mode is enabled.
- Query strings are excluded from request path logging.
- Body capture can be restricted with `body_capture_media_types` allowlist.
- Allowlist-only sanitization is supported with `header_allowlist` and `event_key_allowlist`.

## Middleware Ordering (Body Capture)

When request/response body capture is enabled, install observability middleware as the outermost middleware so it can observe the raw ASGI stream before other middleware consumes it.

## Runtime Control Plane

Set token and enable control plane:

```bash
export OBSERVABILITY_CONTROL_TOKEN="replace-me"
```

```python
from fastapiobserver import RuntimeControlSettings, mount_control_plane

mount_control_plane(app, RuntimeControlSettings(enabled=True))
```

Update runtime settings:

```bash
curl -X POST http://localhost:8000/_observability/control \
  -H "Authorization: Bearer replace-me" \
  -H "Content-Type: application/json" \
  -d '{"log_level":"DEBUG","trace_sampling_ratio":0.25}'
```

## Example App

Run the example:

```bash
uvicorn examples.basic_app:app --reload
```

## Multi-Worker Gunicorn (Prometheus)

For Gunicorn + multiple Uvicorn workers, enable Prometheus multiprocess mode:

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

Without this setup, request counters can appear empty or inconsistent in multi-worker deployments.

## Centralized Monitoring Across Multiple Servers

Yes, this package supports remote FastAPI instances on different servers.

Recommended topology:
- Every FastAPI instance exposes `/metrics` and OTLP traces/logs.
- A central Prometheus scrapes all instances.
- Grafana reads from central Prometheus/Loki/Tempo.

Prometheus target labels (`job`, `instance`) plus this package's metric labels (`service`, `environment`) make cross-server dashboards and filtering straightforward.

## Release Tracks

- `0.1.x`: secure-by-default core
- `0.2.x`: OpenTelemetry-native interoperability
- `1.0.0`: dynamic runtime controls and plugin stability

Current development version:

- `0.2.0.dev1` (security and async logging hardening)

## Changelog Policy

Any breaking change must be called out under a `Breaking Changes` section.

## Packaging and Publishing

### 1) Build distributions

```bash
python -m pip install --upgrade pip build
python -m build
```

This creates:

- `dist/fastapi_observer-<version>.tar.gz`
- `dist/fastapi_observer-<version>-py3-none-any.whl`

### 2) Upload to TestPyPI first

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

If you want strict control during test installs:

```bash
python -m pip install fastapi starlette orjson pydantic-settings
python -m pip install --index-url https://test.pypi.org/simple/ --no-deps fastapi-observer
```

### 4) Upload to production PyPI

```bash
python -m twine upload dist/*
```

The repository also includes GitHub Actions workflows for CI, SBOM generation, and Trusted Publishing.

## Git CLI Workflow

Remote origin is set to:

- `https://github.com/Vitaee/FastapiObserver.git`

Typical commands:

```bash
git add .
git commit -m "feat: implement fastapiobserver library"
git push -u origin main
```

## Roadmap Tracking

See `/Users/canilgu/Desktop/ObservabilityFastapi/NEXT_STEPS.md` for the active `0.2.0` roadmap and release checklist.
