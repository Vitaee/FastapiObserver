# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

## [0.2.2] - 2026-02-19

### Added
- Optional OTLP metrics signal support via `OTelMetricsSettings` and `install_otel_metrics()`.
- Graceful OTel provider lifecycle hooks for flush/shutdown on application exit.
- Middleware span error enrichment for unhandled exceptions (`record_exception` + `StatusCode.ERROR`).
- Documentation and tests for baggage propagation behavior.

## [0.2.1] - 2026-02-19

### Added
- OTel package split with focused modules under `fastapiobserver/otel/`:
  - `settings.py` (OTel settings + env wrappers + runtime sampling state)
  - `resource.py` (resource/excluded URL/exporter helpers)
  - `tracing.py` (`install_otel`)
  - `logs.py` (`install_otel_logs`)
- Backward-compatible private aliases on `fastapiobserver.otel` for pre-split internal symbols used by existing tests/integrations.

### Changed
- Replaced monolithic `src/fastapiobserver/otel.py` with `src/fastapiobserver/otel/` subpackage while preserving public imports from `fastapiobserver.otel`.
- Updated OTel integration tests to patch the new focused submodules directly.

## [0.2.0] - 2026-02-19

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
- Project version advanced to the `0.2.x` line.
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

### Sprint C: OTel/Logs/Metrics Production Hardening

### Added
- Pluggable logging sink architecture with `LogSink` protocol and built-in sinks (`StdoutSink`, `RotatingFileSink`, `LogtailSink`), plus entry-point discovery.
- Outbound trace propagation helpers:
  - `inject_trace_headers()`
  - `instrument_httpx_client()`
  - `instrument_requests_session()`
- OTLP logs environment loader: `OTelLogsSettings.from_env()` with `OTEL_LOGS_*` variables.
- Route-template cardinality tests, excluded-URL precedence tests, OTLP logs pipeline tests, and Accept negotiation tests.
- README production collector pipeline section with processors, sampling, and TLS guidance.

### Changed
- `setup_logging()` now supports `logs_mode` (`local_json`/`otlp`/`both`) and `extra_handlers` routed through a single queue pipeline.
- OTLP log export now reuses provider-scoped idempotency keys instead of global one-time flags.
- OTLP log handler is wrapped in a sanitizing adapter so OTLP attributes align with local redaction policy.
- Excluded URL precedence is now explicit:
  1. `OTEL_EXCLUDED_URLS` setting
  2. OTel env vars (`OTEL_PYTHON_FASTAPI_EXCLUDED_URLS`, `OTEL_PYTHON_EXCLUDED_URLS`)
  3. package defaults
- `OTEL_EXCLUDED_URLS=""` is now treated as an explicit "no exclusions" configuration.
- Metrics middleware now prefers Starlette route templates (`/users/{user_id}`) over raw paths to prevent cardinality explosion.
- `metrics_format="negotiate"` now performs real Accept-based format negotiation between OpenMetrics and Prometheus.

### Fixed
- OTLP-only mode now fails fast when no OTLP log handler can be created, preventing silent log loss.
- Accept header parsing now handles quoted quality factors (for example `q="0"`).
- Prometheus negotiation tests now assert content-type behavior for both positive and negative cases.
- Optional `orjson` import now uses `importlib.import_module` fallback to avoid mypy `import-not-found` CI failures.

### Developer Experience
- Added repo-managed pre-push hook (`.githooks/pre-push`) to run `ruff`, `mypy`, and `pytest` before push.

### Sprint D: Sink Reliability + DRY Utilities

### Added
- Sink circuit-breaker wrapper for all configured output handlers with open/half-open/closed state transitions.
- Prometheus circuit-breaker metrics per sink:
  - `fastapiobserver_sink_circuit_breaker_state_info`
  - `fastapiobserver_sink_circuit_breaker_failures_total`
  - `fastapiobserver_sink_circuit_breaker_skipped_total`
  - `fastapiobserver_sink_circuit_breaker_opens_total`
  - `fastapiobserver_sink_circuit_breaker_half_open_total`
  - `fastapiobserver_sink_circuit_breaker_closes_total`
- Shared utility helpers:
  - `EnvLoadable` mixin for `from_env` boilerplate reduction
  - `parse_csv(...)` for CSV tuple parsing
  - `normalize_protocol(...)` for protocol normalization
  - `lazy_import(...)` for optional dependency guards

### Changed
- `RuntimeControlSettings`, `TrustedProxyPolicy`, and `OTelLogsSettings` now use shared env-loading mixin flow.
- OTel and metrics optional dependency loading now routes through a shared lazy import helper.
- Sink handlers are tagged with stable sink names for metrics and circuit-breaker visibility.

### Sprint E: SOLID Extensibility

### Added
- Log filtering extension point with `LogFilter` protocol plus `register_log_filter()` / `unregister_log_filter()`.
- Metrics backend registry APIs:
  - `register_metrics_backend()`
  - `unregister_metrics_backend()`
  - `get_registered_metrics_backends()`
  - `mount_backend_metrics_endpoint()`
- `METRICS_BACKEND` setting to choose registered backends through `install_observability()`.
- New tests for log filter isolation, custom metrics backend registration, and backend mounting.

### Changed
- Logging queue pipeline now applies a plugin filter stage (`PluginLogFilter`) after request/trace context filters.
- `StructuredJsonFormatter` now supports dependency injection for enrichment and sanitization callables while keeping defaults unchanged.
- `install_observability()` now mounts metrics endpoints through backend capability checks instead of concrete Prometheus type checks.

## [0.1.2] - 2026-02-19

### Added
- Bounded core logging queue controls:
  - `LOG_QUEUE_MAX_SIZE`
  - `LOG_QUEUE_OVERFLOW_POLICY` (`drop_oldest`, `drop_newest`, `block`)
  - `LOG_QUEUE_BLOCK_TIMEOUT_SECONDS`
- Queue pressure observability via `get_log_queue_stats()`.
- Prometheus queue pressure metrics:
  - `fastapiobserver_log_queue_size`
  - `fastapiobserver_log_queue_capacity`
  - `fastapiobserver_log_queue_overflow_policy_info`
  - `fastapiobserver_log_queue_enqueued_total`
  - `fastapiobserver_log_queue_dropped_total{reason=...}`
  - `fastapiobserver_log_queue_blocked_total`
  - `fastapiobserver_log_queue_block_timeouts_total`
- Targeted tests for queue overflow policies and queue metrics registration.

### Changed
- Structured logging pipeline now uses a bounded queue with explicit overflow behavior instead of an unbounded queue.
- README now documents bounded queue controls and queue pressure metrics.

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
