# Roadmap

fastapi-observer should tell you why a FastAPI app is slow, hangs or drops requests in production. JSON logs, request IDs, redaction, metrics and traces are the base for that, not the goal, so new work goes into diagnosis rather than more integrations.

Planned work, in order. Shipped changes are listed in [CHANGELOG.md](CHANGELOG.md). Suggestions are welcome as GitHub issues.

## 2.0: a smaller core

Breaking changes, made once.

- [x] Remove the tamper-evident audit log: the `Audit*` classes, `verify_audit_chain`, the `audit` extra, the `audit_key_provider` argument and `scripts/verify_audit_chain.py`.
- [x] Remove the Logtail sink with its dead-letter queue and `scripts/replay_dlq.py`. The sink circuit breaker stays: sinks added with `register_sink()` can still fail.
- [x] Cut the public API from 77 names to 27.
- [x] Prefix every setting the library owns with `OBS_`. Names like `LOG_LEVEL`, `ENVIRONMENT` and `METRICS_ENABLED` collide with apps' own settings.
- [x] Stop defining non-standard `OTEL_*` variables (`OTEL_ENABLED`, `OTEL_ENVIRONMENT`, `OTEL_TRACE_SAMPLING_RATIO`, `OTEL_LOGS_ENDPOINT`, `OTEL_METRICS_PROTOCOL` and the rest) and read the specification's instead: `OTEL_SDK_DISABLED`, `OTEL_RESOURCE_ATTRIBUTES`, `OTEL_TRACES_SAMPLER_ARG`, `OTEL_EXPORTER_OTLP_PROTOCOL` and the per-signal `OTEL_EXPORTER_OTLP_{TRACES,METRICS,LOGS}_{ENDPOINT,PROTOCOL}`.
- [ ] Decide whether to require FastAPI 0.142 and drop the `FastAPIInstrumentor` fallback.
- [x] Mark the package `Development Status :: 4 - Beta` until every claim in the README has a test behind it, then `5 - Production/Stable`.

### Bugs

- [x] Excluding `/health` also excludes `/healthcare`, and `/metrics` excludes `/api/metrics/x`: trace exclusions match anywhere in the path.
- [x] An explicitly empty `metrics_exclude_paths` is replaced by the defaults.
- [x] OTLP log and metric export ignore `OTEL_EXPORTER_OTLP_PROTOCOL` and default to gRPC.
- [x] In Prometheus multiprocess mode the library's own metrics (log queue, thread pool, database pools) are dropped without a warning.
- [x] Exemplars carry the trace ID of unsampled requests, so they link to traces that were never exported.
- [x] A path segment that starts with digits is collapsed: `/auth/2fa` is labelled `/auth/:idfa`.
- [x] Nothing guards against calling `install_observability()` twice on one app.

### Tests and CI

- [ ] Find out why scheduled workflows don't run: Runtime Stress last ran on 13 July 2026 and Upstream has never run.
- [x] Test against the lowest supported versions of every dependency.
- [x] Test with and without `orjson`.
- [x] Build the wheel in pull-request CI.
- [x] Test that request context doesn't leak between concurrent requests.
- [x] Test across a real `os.fork()`.
- [x] Test Prometheus multiprocess mode with real worker processes.
- [x] Stop generating an SBOM that was never kept.
- [x] Update the contributing guide, which still describes 1.3.1, and remove `scripts/deploy_pypi.sh`: releases use Trusted Publishing, not a token.

Done when every claim in the README has a test behind it.

## Diagnosis

- [ ] Count queries and outbound HTTP calls from SQLAlchemy and `httpx` events instead of OpenTelemetry spans, so `db.n_plus_one` and the per-request counts work with tracing off and see every request, not only sampled ones.
- [ ] Turn the event-loop watchdog on in the `production` profile, at a threshold whose overhead has been measured, and decide whether `LOG_LEVEL_ON_ERROR` belongs there too.
- [ ] Abandoned requests: notice when the client disconnects (`http.disconnect`) even if nothing cancels the handler, and log how long the request kept running.
- [ ] Shutdown: on SIGTERM, log the requests in flight, how long draining took and which requests were cut off.
- [ ] One named warning per failure mode, with the numbers behind it and a `docs_url` pointing at the fix. Named today: `event_loop.blocked`, `threadpool.saturated`, `db.pool_exhausted`, `db.n_plus_one`, `request.cancelled`. Still missing: client disconnect, load-balancer timeout, requests cut off at shutdown.

Done when every common failure mode has a named warning.

## Failure lab

- [ ] A test per failure mode that triggers it and asserts its warning, run in CI: blocked event loop, thread-pool and database-pool exhaustion, a thread-pool and database deadlock, client disconnect, load-balancer timeout, SIGTERM under load, N+1 queries.
- [ ] A Docker Compose stack that triggers each one on demand and shows the log line and dashboard panel that catch it.
- [ ] Docs organised by symptom ("latency climbs while CPU stays low", "5xx bursts during deploys"), starting from the [slow-request diagnosis diagram](docs/architecture.md#diagnosing-slow-but-cpu-is-low).
- [ ] Overhead benchmark in CI with a regression threshold.
- [ ] A README that leads with diagnosis, with a 30-second demo.

Done when anyone can reproduce any failure in five minutes.

## Later

- `observe_job()` for Celery, arq and cron jobs: the same line format, with the request ID read from message headers.
- A `diagnose` command or MCP tool that explains recent warnings, if a `docs_url` on each warning isn't enough for an assistant to work from.

## Not planned

Left out so the library goes deeper rather than wider:

- New sinks, vendor integrations, or tracing and metrics features that FastAPI or OpenTelemetry already provide.
- A health or readiness endpoint helper.
- Control-plane audit trails, pluggable control-plane authentication and runtime sampling toggles.
