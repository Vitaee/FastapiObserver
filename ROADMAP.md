# Roadmap

Planned work, roughly in priority order. Shipped changes are listed in [CHANGELOG.md](CHANGELOG.md). Suggestions are welcome as GitHub issues.

## Leaner core

- [ ] Hand work that OpenTelemetry, `prometheus_client` or the standard library already do to them, keeping the public API unchanged.
- [ ] Opt-in sampling of successful request logs (errors and slow requests always logged).
- [ ] Free-threaded Python (3.14t) in the CI matrix.

## Performance and reliability

- [ ] Middleware overhead benchmark in CI with a regression threshold.
- [ ] High-concurrency tests for request-context isolation.
- [ ] Graceful fallback if mounting the Prometheus endpoint fails.
- [ ] Warn when observability was initialised before a process fork (compare `os.getpid()` at init and at request time).

## Developer experience

- [ ] Health and readiness endpoint helper.
- [ ] Middleware-ordering section in the examples docs.
- [ ] Example `gunicorn` deployment showing worker lifecycle and multiprocess metrics.

## Runtime controls

- [ ] Audit trail sink for control-plane changes.
- [ ] Runtime toggle for per-path sampling profiles.
- [ ] Pluggable authentication for the control plane, beyond tokens.

## Release pipeline

- [ ] TestPyPI release candidates before each stable release.
- [ ] Log schema compatibility check as a release gate.
