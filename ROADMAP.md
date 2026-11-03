# Roadmap

fastapi-observer should tell you why a FastAPI app is slow, hangs or drops requests in production. JSON logs, request IDs, redaction, metrics and traces are the base for that, not the goal, so new work goes into diagnosis rather than more integrations.

Planned work, in order. Shipped changes are listed in [CHANGELOG.md](CHANGELOG.md). Suggestions are welcome as GitHub issues.

## Left from 2.0

2.0.0 shipped the smaller core: one name per setting, a 27-name API, and no audit log or Logtail sink. Still open:

- [ ] Back every claim in the README with a test, then mark the package `Development Status :: 5 - Production/Stable`.
- [ ] Find out why scheduled workflows don't run: Runtime Stress last ran on 13 July 2026 and Upstream has never run.
- [ ] Decide whether to require FastAPI 0.142 and drop the `FastAPIInstrumentor` fallback.

## Diagnosis

- [ ] Count outbound HTTP calls from `httpx` events, so the `http` totals work with tracing off too.
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
