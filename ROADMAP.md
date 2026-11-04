# Roadmap

fastapi-observer should tell you why a FastAPI app is slow, hangs or drops requests in production. JSON logs, request IDs, redaction, metrics and traces are the base for that, not the goal, so new work goes into diagnosis rather than more integrations.

Planned work, in order. Shipped changes are listed in [CHANGELOG.md](CHANGELOG.md). Suggestions are welcome as GitHub issues.

## Current state and next steps

As of 4 November 2026, the latest release is **2.2.0**. Response/application
timing, tracing-independent HTTP diagnostics and application-owned logging have
shipped. The package remains **Beta** while the production evidence below is
incomplete.

The unreleased **2.2.1** hardening change adds privacy/fork fixes, bounded SQL
tracking and log drain waits, observed-disconnect diagnostics, and shared
PR/release production gates. The gates cover real-server failures, installed
wheel SQL tracing, backend export with traces on/off and paired CPU overhead.

The next focused work is:

1. **Shutdown attribution:** retain server ownership of signals and draining,
   while investigating how to report in-flight requests interrupted during a
   deployment without inferring a cause from cancellation alone. Observed client
   disconnects are covered; an application that never reads after a client leaves
   can still leave that disconnect unobserved.
2. **A README claim audit:** link each claim to evidence or qualify it before
   changing the stability status. Include the limitations of trace privacy,
   multiworker pressure metrics and arbitrary custom sink blocking.
3. **Validate the hardening release in an established application** with its
   existing sanitizer and telemetry providers. Confirm correlation, one event per
   intended destination and acceptable overhead in that workload. Worker
   diagnostics stay behind the validation gate below.

## Application integration

- [x] Install request diagnostics with `configure_logging=False`, retaining the application's handlers, filters, formatters, levels, sanitizer and log export. Cover application-owned queues and existing OpenTelemetry providers with regression tests and a runnable JSON logger example.
- [x] State the limits of field-name redaction and exception URL-credential masking, and show how to retain an approved free-text sanitizer and its test vectors.

## Left from 2.0

2.0.0 shipped the smaller core: one name per setting, a 27-name API, and no audit log or Logtail sink. Still open:

- [ ] Back every claim in the README with a test, then mark the package `Development Status :: 5 - Production/Stable`.
- [x] Verify scheduled Runtime Stress and Upstream workflows run (confirmed 3 November 2026).
- [ ] Decide whether to require FastAPI 0.142 and drop the `FastAPIInstrumentor` fallback.

## Diagnosis

- [x] Count outbound HTTP calls independently of tracing, including failures and timeout categories for HTTPX, HTTPX2 and Requests.
- [x] Observed abandoned requests: record `http.disconnect` received before response completion, and how long the app kept working. Do not add a reader or claim detection when the app never receives the event.
- [ ] Shutdown: on SIGTERM, log the requests in flight, how long draining took and which requests were cut off.
- [ ] One named warning per failure mode, with the numbers behind it and a `docs_url` pointing at the fix. Named today: `event_loop.blocked`, `threadpool.saturated`, `db.pool_exhausted`, `db.n_plus_one`, `request.cancelled`. Still missing: client disconnect, load-balancer timeout, requests cut off at shutdown.

Done when every common failure mode has a named warning.

## Failure lab

The [full-stack example](examples/full_stack/README.md) reproduces a blocked event loop,
database-pool exhaustion and N+1 queries, with real-server tests asserting their warnings.
The other failure modes below remain open.

The lab also covers slow downstreams, streaming failures and background work.
HTTP response duration is measured separately from total application lifetime;
later failures keep their correlation and the actual sent response status.

- [x] Real-server tests for blocked event loops, database-pool exhaustion and N+1 queries.
- [x] Real-server regressions for response/application timing, background failures, slow and failed downstreams, streaming and concurrent requests.
- [x] Real-server regressions for observed disconnect, concurrent SIGTERM drain/cancellation, Collector outage, warm-worker memory limits and worker recycling.
- [ ] Remaining real-server failure modes: thread-pool exhaustion, a thread-pool and database deadlock, and load-balancer timeout.
- [ ] A Docker Compose stack that triggers each one on demand and shows the log line and dashboard panel that catch it.
- [ ] Docs organised by symptom ("latency climbs while CPU stays low", "5xx bursts during deploys"), starting from the [slow-request diagnosis diagram](docs/architecture.md#diagnosing-slow-but-cpu-is-low).
- [x] Overhead benchmark in shared CI/release validation with paired measurements and a variance-aware threshold.
- [ ] A README that leads with diagnosis, with a 30-second demo.

Done when anyone can reproduce any failure in five minutes.

## Later

- `observe_job()` only after a worker user demonstrates a diagnostic need that context-rich JSON logging alone does not meet. See the validation gate below.
- A `diagnose` command or MCP tool that explains recent warnings, if a `docs_url` on each warning isn't enough for an assistant to work from.

### Validate worker diagnostics before implementing them

Standalone logging works outside FastAPI; database and HTTP diagnostic totals
currently belong to request state. A worker-heavy service needing tenant, run
and node fields in its logs may only need to extend its existing JSON formatter.
That need alone does not justify a new dependency or job instrumentation.

Before building `observe_job()`, obtain a concrete example from a prospective
worker user:

- An incident or operational question their existing logs cannot answer, and
  which job duration, failure or database/HTTP totals would answer.
- A representative job boundary and its existing correlation/redaction contract,
  with a working destination for the diagnostic output.
- A comparison against the smaller change of adding structured context to their
  current logger, and agreement to try the proposed diagnostic in that workload.

No worker extension is implemented or promised until that evidence exists. If
validated, the first scope is a small context manager reporting duration,
failures, correlation fields and database/HTTP totals independently of requests
and tracing. Existing worker code keeps ownership of retries, acknowledgements
and scheduling; framework-specific integrations need separate evidence.

## Not planned

Left out so the library goes deeper rather than wider:

- New sinks, vendor integrations, or tracing and metrics features that FastAPI or OpenTelemetry already provide.
- A health or readiness endpoint helper.
- Control-plane audit trails, pluggable control-plane authentication or runtime enable/disable of telemetry pipelines. Existing log-level and trace-sampling-ratio controls remain supported.
