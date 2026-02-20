# Next Steps (v0.2.0.dev1+)

This file tracks what is completed and what remains before stable `0.2.x`.

## Completed

### OTel
- [x] Log correlation test coverage (`tests/test_otel_log_correlation.py`).
- [x] OTLP collector fixture + integration test (`tests/conftest_otlp.py`, `tests/test_otlp_export_integration.py`).
- [x] Configurable OTel resource attributes (`extra_resource_attributes`, `OTEL_EXTRA_RESOURCE_ATTRIBUTES`).
- [x] Coexistence logic when a host app already configured a global tracer provider.

### Security
- [x] Allowlist-only sanitization mode (`header_allowlist`, `event_key_allowlist`).
- [x] Redaction presets (`strict`, `pci`, `gdpr`) with tests.
- [x] Body media-type allowlist for capture.
- [x] Preset override behavior from env, including explicit unsetting with `none|null|unset` for optional allowlists.
- [x] Preset metadata/constants exported from package API.

### Logging / Middleware
- [x] Queue-based logging pipeline (`QueueHandler` + `QueueListener`) for async-friendly request path behavior.
- [x] Structured error classification (`error_type`, `exception_class`).
- [x] Middleware ordering warning when body capture is enabled late.

## Open Work

### Dynamic Runtime Controls
- [ ] Add optional audit trail sink for control-plane changes.
- [ ] Add runtime toggle for per-path sampling profiles.
- [ ] Add auth extensibility interface for non-token control-plane auth.

### Performance & Reliability
- [ ] Add middleware overhead benchmark with CI regression threshold.
- [ ] Add high-concurrency tests for context isolation.
- [ ] Add graceful fallback behavior if Prometheus endpoint mount fails.
- [ ] Investigate runtime detection heuristics to warn users if observability was initialized before a process fork (e.g., comparing `os.getpid()` at init vs request time).

### DX / Platform
- [ ] Add first-class health/readiness helper endpoint integration.
- [ ] Add explicit middleware-ordering section to examples docs (beyond runtime warning).
- [ ] Create a dedicated `gunicorn.md` deployment guide covering safe initialization with `--preload-app`, `uvloop`, and multiprocessing metrics.
- [ ] Add a `gunicorn` example deployment script to `examples/` demonstrating best practices with worker lifecycle management.

### Release Pipeline
- [ ] Configure Trusted Publishing for TestPyPI and PyPI.
- [ ] Add release gates:
  - changelog entry
  - schema compatibility check
  - test matrix green
  - SBOM generated
- [ ] Publish and validate `0.2.0.dev1` on TestPyPI, then cut stable `0.2.x`.
