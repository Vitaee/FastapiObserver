# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

## [1.3.1] - 2026-02-27

### Fixed
- Prometheus multiprocess compatibility on environments where `prometheus_client.multiprocess`
  is not attached to the root module until explicitly imported.
- Added internal multiprocess preloading shim so `mark_process_dead(...)` and metrics endpoint
  mounting work without client-side monkey patches when `PROMETHEUS_MULTIPROC_DIR` is enabled.

## [1.3.0] - 2026-02-27

### Added
- Zero-glue installation path via `install_observability(app)` with environment-driven auto-loading for settings, security policy, trusted proxy policy, OTel settings, and runtime control settings.
- Environment profile support (`OBS_PROFILE`) with built-in `development` and `production` defaults through `fastapiobserver.profiles.apply_profile_context`.
- Public FastAPI lifespan helper `observability_lifespan` for explicit lifecycle integration.
- Route exclusion auto-discovery for utility endpoints and `include_in_schema=False` routes, including dynamic path normalization variants for telemetry filters.
- New tests for profiles and teardown behavior:
  - `tests/test_profiles.py`
  - `tests/test_lifespan_teardown.py`

### Changed
- `install_observability` now accepts optional `settings` and resolves defaults from env when omitted.
- Teardown flow is now centralized and idempotent via `_teardown_observability(...)`, and runs with `try/finally` semantics in lifespan wrappers.
- Metrics exclusion checks now support both raw and collapsed route paths and can read discovered exclusions from app state.
- Active OTel middleware exclusions are updated at lifespan-time route discovery so hidden routes are consistently excluded from tracing.
- Documentation was reorganized into the `docs/` directory with expanded zero-glue, profiles, architecture, and operations guidance.

## [1.2.0] - 2026-02-21

### Added
- Tamper-evident audit logging support with hash-chain formatter, pluggable key providers, and verification helpers under `fastapiobserver.audit`.
- SQLAlchemy tracing helpers with optional SQLCommenter enrichment via `fastapiobserver.db_tracing` for sync and async engines.
- New optional dependency groups:
  - `fastapi-observer[audit]` for local HMAC audit-chain signing.
  - `fastapi-observer[otel-sqlalchemy]` for SQLAlchemy OpenTelemetry instrumentation.
- New runnable examples and utilities:
  - `examples/audit_app.py`
  - `examples/db_tracing_app.py`
  - `scripts/verify_audit_chain.py`

### Changed
- `install_observability()` now accepts `audit_key_provider`, `db_engine`, `db_commenter_enabled`, and `db_commenter_options` for one-call audit + DB tracing setup.
- `all` extra now includes both `cryptography` and SQLAlchemy OTel instrumentation dependencies.

## [1.0.0] - 2026-02-21

### Added
- Stable `1.0.0` release milestone with the existing extension surfaces (`LogSink`, log filters, and pluggable metrics backends) promoted for long-term use.

### Changed
- Promoted package metadata classifier from alpha to `Production/Stable`.
- Consolidated the hardened `0.4.x` line into the first stable contract, including package modularization, lock-safe sink registry/discovery behavior, and runtime control token rotation handling.

## [0.4.1] - 2026-02-21

### Fixed
- Patch release cut for new PyPI artifacts (0.4.0 artifacts are immutable once published).

## [0.4.0] - 2026-02-21

### Added
- README contributor map documenting the new internal package layout (`logging/`, `middleware/`, `sinks/`, `metrics/`, `security/`, `otel/`).

### Changed
- Split monolithic modules into focused subpackages for logging, middleware, sinks, metrics, and security while preserving public imports through package facades.
- Metrics builder now uses registry accessors instead of importing registry private state directly.
- Sink registry operations are now lock-protected and factory assembly now reads from registry snapshots.
- Sink discovery now uses lock-protected double-checked initialization for thread-safe entry-point discovery.
- Logtail handler now accounts for high-contention requeue drops and routes those dropped records to DLQ when enabled.
- Logtail DLQ now exposes `get_stats()` to avoid direct private-state access from handler code.
- Runtime control-plane token authorization now re-reads the configured token env var on each request to support token rotation.

## [0.3.2] - 2026-02-20

### Added
- Added advanced benchmark applications and a benchmark runner mode for realistic request-body + OTLP measurement.
- Added `.env.example` with a production-focused observability settings template.

### Changed
- Reduced header decoding overhead in middleware by reading needed headers directly from raw ASGI header bytes.
- Cached trusted CIDR resolution decisions in `is_trusted_client_ip()` for faster repeated proxy checks.
- Expanded README and benchmark documentation with high-throughput tuning and Gunicorn preload safety guidance.


## [0.3.1] - 2026-02-19

### Fixed
- CI compatibility for async tests on Python 3.14+ by removing reliance on `pytest-asyncio` markers in test files.
- Converted async-marked tests to synchronous wrappers using `asyncio.run(...)` so test execution no longer depends on optional pytest async plugins.
- Removed `asyncio_mode` from `pyproject.toml` pytest config to avoid plugin-specific warnings in environments without `pytest-asyncio`.

## [0.3.0] - 2026-02-19

### Added
- **AST Error Fingerprinting**: Exception pipelines now sanitize transient data (memory addresses like `0x10a2b...` and exact line numbers) to generate a stable `error.fingerprint` hash. This allows zero-dependency grouping of identical server errors directly in external dashboards.
- **Native GraphQL Observability (Strawberry)**: Ships with a zero-dependency duck-typed `StrawberryObservabilityExtension`. Add this to your `strawberry.Schema` to automatically extract `operationName` from `POST /graphql` queries and record it in JSON logs and OTel traces.
- **Logtail Dead Letter Queue (DLQ)**: Implemented best-effort local durability for the Logtail sink. Under queue overflow (`queue.Full`) or network partitions (HTTP backoff exhaustion), dropped messages are now transparently archived to rotating, gzipped local files (`.dlq/logtail/*.ndjson.gz`) with specific Prometheus annotations (`fastapiobserver_dlq_written_total`). Includes a `scripts/replay_dlq.py` utility for operational data recovery.

## [0.2.5] - 2026-02-19

### Changed
- Packaging version source now uses PEP 621 dynamic metadata from `fastapiobserver._version.__version__` to avoid dual-source drift.
- Added explicit Ruff configuration (`target-version`, `line-length`, and selected rules) in `pyproject.toml` for consistent lint behavior across contributors/CI.
- Logging queue listener lifecycle is now closed gracefully via both FastAPI shutdown hook registration and `atexit` fallback.

### Added
- Public `shutdown_logging()` helper for explicit logging pipeline teardown in application lifecycle hooks and tests.

## [0.2.4] - 2026-02-19

### Added
- OTel typing protocols for optional integrations (`otel/types.py`) to improve IDE support without hard runtime OTel imports.

### Changed
- OTel helper return annotations in `otel/resource.py` now use protocol-based types instead of broad `Any` where practical.
- Exception swallow paths now emit debug diagnostics with `exc_info=True` in:
  - `TraceContextFilter` OTel span lookup fallback
  - sink entry-point discovery outer fallback
  - Logtail retryable send failure path

## [0.2.3] - 2026-02-19

### Added
- Structured top-level `error` payload in exception logs with `error.type`, `error.message`, and `error.stacktrace`.
- OTel HTTP semantic-convention aliases in middleware events:
  - `http.request.method`
  - `url.path`
  - `http.response.status_code`
- Loguru coexistence bridge helpers:
  - `install_loguru_bridge()`
  - `remove_loguru_bridge()`
  - `build_loguru_to_stdlib_sink()`
- Optional `loguru` dependency extra (`fastapi-observer[loguru]`).
- Loguru coexistence guide (`loguru.md`) and benchmark guide (`benchmarks.md`) with runnable benchmark harness under `examples/benchmarks/`.

### Changed
- README now includes a CI badge, compatibility matrix, Loguru coexistence section, and benchmarking links.

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
