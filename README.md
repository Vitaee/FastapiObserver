# observabilityfastapi

Secure-by-default observability toolkit for FastAPI apps:
- Structured JSON logs
- Request correlation (`x-request-id`)
- Optional Prometheus metrics
- Optional OpenTelemetry tracing
- Runtime control plane with token auth
- Plugin hooks for custom enrichers and metric hooks

## Install

Core:

```bash
pip install observabilityfastapi
```

With Prometheus support:

```bash
pip install "observabilityfastapi[prometheus]"
```

With OpenTelemetry support:

```bash
pip install "observabilityfastapi[otel]"
```

Everything:

```bash
pip install "observabilityfastapi[all]"
```

## Quick Start

```python
from fastapi import FastAPI
from observabilityfastapi import (
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

## Runtime Control Plane

Set token and enable control plane:

```bash
export OBSERVABILITY_CONTROL_TOKEN="replace-me"
```

```python
from observabilityfastapi import RuntimeControlSettings, mount_control_plane

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

## Release Tracks

- `0.1.x`: secure-by-default core
- `0.2.x`: OpenTelemetry-native interoperability
- `1.0.0`: dynamic runtime controls and plugin stability

Current development version:

- `0.2.0.dev0` (post-`v0.1.0` hardening and compatibility work)

## Changelog Policy

Any breaking change must be called out under a `Breaking Changes` section.

## Packaging and Publishing

### 1) Build distributions

```bash
python -m pip install --upgrade pip build
python -m build
```

This creates:

- `dist/observabilityfastapi-<version>.tar.gz`
- `dist/observabilityfastapi-<version>-py3-none-any.whl`

### 2) Upload to TestPyPI first

```bash
python -m pip install --upgrade twine
python -m twine upload --repository testpypi dist/*
```

### 3) Validate install from TestPyPI

```bash
python -m pip install --index-url https://test.pypi.org/simple/ --no-deps observabilityfastapi
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
git commit -m "feat: implement observabilityfastapi library"
git push -u origin main
```

## Roadmap Tracking

See `/Users/canilgu/Desktop/ObservabilityFastapi/NEXT_STEPS.md` for the active `0.2.0` roadmap and release checklist.
