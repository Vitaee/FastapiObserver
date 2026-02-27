# Maintenance & Contributing


## OTel Test Coverage

Repository integration tests include:
- `tests/test_otel_log_correlation.py`: verifies trace/span IDs in logs map to real spans.
- `tests/test_otlp_export_integration.py`: validates OTLP HTTP export with local collector fixtures.

---

## Benchmarking

Reproducible benchmark harness and methodology:
- Guide: [`benchmarks.md`](benchmarks.md)
- Apps: `examples/benchmarks/app.py`
- Runner: `examples/benchmarks/harness.py`

---

## Release Tracks

- `0.1.x`: secure-by-default core
- `0.2.x`: OTel interoperability, security presets, allowlists
- `0.3.x`: GraphQL observability, error fingerprinting, and Logtail DLQ durability
- `0.4.x`: package modularization, sink/registry hardening, and runtime control token rotation
- `1.0.x`: first stable release contract for production deployments
- `1.2.0`: tamper-evident audit logging and SQLAlchemy trace/commenter integration
- `1.3.0`: zero-glue install defaults, profile-aware configuration, lifespan teardown hardening, and route exclusion auto-discovery
- `1.3.1`: prometheus-client multiprocess compatibility fix for delayed submodule attachment

Current release version: `1.3.1`

## Changelog Policy

Breaking changes must be listed under a `Breaking Changes` section in `CHANGELOG.md`.

---

## Packaging and Publishing (Maintainers)

Recommended release flow (Trusted Publishing + auto GitHub release notes):

```bash
git tag v1.3.1
git push origin v1.3.1
```

On tag push, `.github/workflows/release.yml` will:

1. Build and validate package artifacts.
2. Publish to PyPI via Trusted Publishing.
3. Create/update the GitHub Release body from the matching section in `CHANGELOG.md`.

Manual fallback command (uses `.env` with `PYPI_TOKEN`):

```bash
scripts/deploy_pypi.sh --tag v1.3.1 --push-tag
```

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

See [NEXT_STEPS.md](https://github.com/Vitaee/FastapiObserver/blob/main/NEXT_STEPS.md) for the active roadmap and release checklist.
