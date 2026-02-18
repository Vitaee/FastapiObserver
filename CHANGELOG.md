# Changelog

All notable changes to this project will be documented in this file.

## [0.2.0.dev1] - Unreleased

### Sprint A: Baseline Hardening

### Added
- Explicit log schema version (`log_schema_version`) and package version metadata in every JSON log.
- Public package version export (`fastapiobserver.__version__`).
- Prometheus metric labels now include `service` and `environment` for better cross-instance aggregation.
- Gunicorn helper `mark_prometheus_process_dead()` to support Prometheus multiprocess cleanup.
- Prometheus multiprocess directory validation when `PROMETHEUS_MULTIPROC_DIR` is enabled.
- Environment-based constructors:
  - `SecurityPolicy.from_env()`
  - `TrustedProxyPolicy.from_env()`
  - `OTelSettings.from_env()`
  - `RuntimeControlSettings.from_env()`
- Validation/normalization for settings and policy inputs:
  - log level validation
  - header/path normalization
  - OTel protocol and sampling validation
  - security redaction mode and body length checks
- New tests for configuration and environment loading.

### Changed
- Project version bumped to `0.2.0.dev1` for next development cycle.
- Distribution name changed from `fastapiobserver` to `fastapi-observer` (import path remains `fastapiobserver`).
- Dependency minimums refreshed to currently tested releases (FastAPI/Starlette/OTel/Prometheus/tooling).
- Python compatibility range changed to `>=3.10,<3.15`.
- CI matrix expanded to run tests on Python `3.10` through `3.14`.
- Removed deprecated license classifier to comply with modern setuptools/PEP 639 validation.
- OTel installation now coexists safely with host applications that already configured a global tracer provider.
- Logging pipeline moved to queue-based handlers to reduce request-path blocking from synchronous I/O.

### Sprint B: OTel & Security Test Depth

### Added
- Security policy presets: `strict`, `pci`, and `gdpr`.
- Allowlist-only sanitization options (`header_allowlist`, `event_key_allowlist`).
- Body capture media type allowlist support (`body_capture_media_types`).
- OTel custom resource attribute support (`extra_resource_attributes` and `OTEL_EXTRA_RESOURCE_ATTRIBUTES`).
- Middleware error classification fields (`error_type`, `exception_class`).
- OTel log-correlation integration test (`tests/test_otel_log_correlation.py`).
- OTLP collector fixture and OTLP export integration test (`tests/conftest_otlp.py`, `tests/test_otlp_export_integration.py`).
- Public exports for security preset constants (`SECURITY_POLICY_PRESETS`, `PCI_REDACTED_FIELDS`, `GDPR_REDACTED_FIELDS`, `STRICT_HEADER_ALLOWLIST`).

### Changed
- Security env override behavior now checks actual env var presence before applying overrides.
- Optional preset fields can be explicitly unset via `none|null|unset` values.
- OTel integration tests reset global tracer provider state between tests to avoid cross-test exporter leakage.

## [0.1.0] - 2026-02-18

### Added
- Secure-by-default structured logging middleware for FastAPI.
- Request ID propagation with trust-boundary controls.
- Optional Prometheus metrics backend and `/metrics` endpoint mounting.
- Optional OpenTelemetry install helpers and dynamic trace sampling controls.
- Runtime control plane with token authentication.
- Plugin hooks for custom log enrichers and metric hooks.

### Breaking Changes
- None.
