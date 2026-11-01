# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Fixed
- On FastAPI 0.142+, the Strawberry extension named FastAPI's endpoint span after the GraphQL operation instead of the request's server span, so trace lists still showed `POST /graphql`. It now names and tags the server span, with FastAPI's own tracing and with OpenTelemetry's instrumentation. Affected: 1.12.0 to 2.0.0.
- `.env.example` still used the 1.x variable names and listed the removed Logtail sink. It now uses the 2.0 names and shows the settings for finding slow and failing requests.
- The Kubernetes demo installed fastapi-observer 0.1.1 while setting 2.0 variables, so it traced nothing. It now installs 2.x.
- Docs that no longer matched the code are corrected: `OTEL_TRACES_SAMPLER` and `OTEL_PYTHON_FASTAPI_EXCLUDED_URLS` advice in the tuning guide, what multiprocess mode leaves out of `/metrics`, provider and logging shutdown, gunicorn's `--preload` flag, the supported versions in `SECURITY.md`, and the version the benchmarks were measured with.

## [2.0.0] - 2026-11-01

### Breaking Changes
- Every environment variable the library owns now starts with `OBS_`: `LOG_LEVEL` is `OBS_LOG_LEVEL`, `METRICS_ENABLED` is `OBS_METRICS_ENABLED`, `OTEL_ENABLED` is `OBS_OTEL_ENABLED`, and so on. For OpenTelemetry the library reads the specification's variables instead of its own: `OTEL_TRACES_SAMPLER_ARG`, `OTEL_RESOURCE_ATTRIBUTES`, `OTEL_METRIC_EXPORT_INTERVAL`, and `OTEL_EXPORTER_OTLP_*` for every signal's endpoint and protocol. `OTEL_SERVICE_NAME` now names the service on logs and metrics too, unless `OBS_SERVICE_NAME` is set, and `OTEL_SDK_DISABLED=true` turns all OpenTelemetry export off. At startup, `observability.settings_retired` lists any 1.x variable still set with its replacement. **Every deployment needs its variables renamed**; see the upgrade table in the configuration guide and decision record 0013.
- The top-level `fastapiobserver` package exports 27 names instead of 77. The rest are still importable from their submodules, such as `fastapiobserver.security.sanitize_event` or `fastapiobserver.db_tracing.instrument_sqlalchemy`, without the same promise of stability. **Imports of a removed name need the submodule's path.**
- `OTelSettings` no longer reads `OTEL_SERVICE_VERSION` or `OTEL_ENVIRONMENT`, and `OTelSettings.from_env()` takes no arguments. Traces use `OBS_APP_VERSION` and `OBS_ENVIRONMENT`, which `OTEL_RESOURCE_ATTRIBUTES` can override.
- Calling `install_observability()` a second time on the same app now changes nothing and logs `observability.already_installed`. It used to replace logging and metrics but keep the first call's middleware.

### Security
- A variable named like a settings field was read as that setting: `ENABLED=true` turned on the runtime control plane and OTLP export, and the control plane's path came from `PATH`. Settings are now read only from their documented names. Affected: every earlier release.

### Fixed
- Trace exclusions matched anywhere in the URL, so excluding `/health` also stopped tracing `/healthcare`, and `/metrics` stopped `/api/metrics/x`. They now match whole paths, and hidden routes with path parameters, such as `/items/{item_id}`, are excluded from traces too, as they already were from metrics.
- Exclusions set explicitly, with `OBS_OTEL_EXCLUDED_URLS` or OpenTelemetry's `OTEL_PYTHON_*_EXCLUDED_URLS`, were extended with the discovered routes when the app started. They're now used as given. An empty `OBS_METRICS_EXCLUDE_PATHS` excludes nothing instead of falling back to the defaults.
- OTLP log and metric export ignored `OTEL_EXPORTER_OTLP_PROTOCOL` and always used gRPC unless their own variables were set.
- Exemplars carried the trace ID of requests whose trace wasn't sampled, so they linked to traces that were never exported. Only sampled traces get one now.
- Metric labels collapsed a path segment that only started with digits: `/auth/2fa` was labelled `/auth/:idfa`. Only whole numeric, hex and UUID segments become `:id`.
- In Prometheus multiprocess mode, the log queue, log volume, sink, thread pool and database pool metrics were left out without a word. Startup now logs `metrics.multiprocess.process_stats_unavailable`.
- `OBS_PROFILE` cleared and restored the whole process environment while settings were read, so changes other threads made meanwhile were lost. It now adds and removes only its own defaults.

### Removed
- The tamper-evident audit log: `AuditChainFormatter`, `AuditKeyProvider`, `LocalHMACProvider`, `verify_audit_chain`, `AuditVerificationResult`, the `audit` extra, the `audit_key_provider` argument, the `OBS_AUDIT_*` settings and `scripts/verify_audit_chain.py`.
- The Logtail sink and its dead-letter queue: `LogtailSink`, the `LOGTAIL_*` settings, the `fastapiobserver_dlq_*` metrics and `scripts/replay_dlq.py`. To keep sending logs to Better Stack, register a handler of your own with `register_sink()`.
- `scripts/deploy_pypi.sh`, which uploaded with a token. Releases use Trusted Publishing.

### Changed
- The package is marked `Development Status :: 4 - Beta` until every claim in the README has a test behind it.
- CI also tests the oldest dependency versions `pyproject.toml` allows and the `orjson` encoder, and builds and installs the wheel on every pull request. It no longer generates an SBOM that was never kept.

## [1.16.0] - 2026-11-01

### Fixed
- The Strawberry extension made every GraphQL request fail with a 500 on current Strawberry (0.339.1). It didn't subclass Strawberry's `SchemaExtension`, and Strawberry calls methods it lacked. It now subclasses it, works with both sync and async execution, and reads the operation name after the query is parsed, so an operation named only in the query text is found. Its tests now run against real Strawberry instead of mocks.
- The runtime control plane answered an `Authorization` header with non-ASCII characters with a 500 instead of a 401.
- The runtime control plane accepted a `trace_sampling_ratio` and reported it applied even when nothing used it: with tracing off, or with a tracer provider the app set up itself. It now answers `409`, and `GET` returns `null` for the ratio.

### Added
- The runtime control plane logs every change as `control_plane.settings_changed`, with what was requested, the settings it replaced, the caller's IP address and the process ID. It's written before the change, so raising the log level can't drop it. Responses include `process_id`, since a change applies to the process that served the request.

### Removed
- Log sinks from installed packages: the `fastapiobserver.log_sinks` entry-point group, the `OBS_LOG_SINKS` setting (`ObservabilitySettings.log_sinks`) and `fastapiobserver.sinks.discover_entry_point_sinks`. No package is known to provide one. Register a custom sink in code with `register_sink()`. If `OBS_LOG_SINKS` is still set, startup logs `observability.log_sinks_removed`. **Apps that loaded a sink this way need one `register_sink()` call.** See decision record 0012.

### Changed
- Importing `fastapiobserver.integrations.strawberry` without Strawberry installed now raises `ImportError`. The extension couldn't work without Strawberry before either.

## [1.15.0] - 2026-11-01

### Security
- With body capture on (`OBS_LOG_REQUEST_BODY` or `OBS_LOG_RESPONSE_BODY`), captured bodies were written without redaction, so a login request's `password` or a returned `token` appeared in plain text. Values of sensitive keys are now redacted in JSON and form bodies, including a value the length limit cut off. Affected: every earlier release with body capture on.

### Fixed
- Requests that matched no route kept their raw path as the metrics `path` label unless they were 404s, and the `method` label was copied from the request. Any anonymous client could add time series without limit, with CORS preflights to random paths, requests an auth middleware rejects, or made-up methods. Every unmatched request is now `<unmatched>`, and methods outside the nine standard ones are `_OTHER`. Plain Starlette routes are labelled by their template instead of their path. **Dashboards that filter on plain routes' old labels need updating.** See decision record 0011.
- When one app's lifespan ended, it removed the root log handlers and shut down the global OpenTelemetry providers. Other apps in the process, and the same app started again by a reused `TestClient`, then wrote no logs and exported no spans, with no error. The lifespan now writes out what's queued and flushes the providers, and leaves both running; process exit stops them. It also no longer removes SQLAlchemy instrumentation you set up yourself.
- An explicit `OTelSettings(enabled=True)` sent traces as service `api`, version `0.0.0` and environment `development`, while logs and metrics used the values from `ObservabilitySettings`. Its `service_name`, `service_version` and `environment` now default to those values.
- OTLP-exported log records had no trace ID, so backends couldn't link them to traces: they're exported on the log queue's listener thread, where no span is current. They now carry the trace and span of the code that wrote them.
- The gunicorn docs said workers drop logs under `--preload-app`, and suggested calling `install_observability()` in the lifespan, which fails with `Cannot add middleware after an application has started`. Logs work with `--preload-app`. The docs now say so, and recommend OTLP over HTTP with it, since gRPC channels don't survive a fork.
- The weekly runtime stress harness imported `httpx`, which the dev dependencies no longer install, so its next run would have failed.

### Changed
- Releases run the test suite before publishing, and only the publish job can write to the repository.

## [1.14.0] - 2026-11-01

### Security
- `hash` redaction, used by the `gdpr` preset, hashed values with plain SHA-256. Emails, phone numbers and IP addresses hashed that way can be recovered by hashing candidates until one matches. Values are now HMAC-SHA256 with the key in `OBS_REDACTION_HASH_KEY` or `SecurityPolicy(hash_key=...)`, written as `hmac-sha256:<hex>` instead of `sha256:<hex>`. Without a key, a random one is picked at startup and `observability.redaction.hash_key_missing` is logged. **Hashes change once on upgrade; queries that match `sha256:` need updating.** Affected: every earlier release that used the `gdpr` preset or `OBS_REDACTION_MODE=hash`.

### Fixed
- A request cancelled before it finished, because the client left, the server shut down or a timeout fired, was logged as `request.server_error` with status 500 and counted as a 500 in metrics. It is now `request.cancelled` at `WARNING`, with status 499 when no response had started.
- `error.fingerprint` included the exception message, so the same bug with different IDs in its message got a different fingerprint each time. It now hashes the exception type and each frame's file, function and source line. **Fingerprints change once on upgrade.**
- After a fork, the child could deadlock writing logs: restarting the log listener drained the inherited queue, whose lock another thread of the parent could have held at the moment of the fork, and the queue-statistics and sink circuit-breaker locks were inherited the same way. The child now gets fresh locks and a fresh queue without touching the old one.

## [1.13.0] - 2026-11-01

### Added
- A `threadpool.saturated` warning when every thread in AnyIO's pool, which runs sync endpoints and dependencies, is busy and calls are waiting. A `db.pool_exhausted` warning when every connection in a SQLAlchemy `QueuePool` passed as `db_engine` is checked out. Both are checked as each request starts and written at most once a minute per pool, so a deadlock between the two pools is reported too. With metrics on, `/metrics` has `fastapiobserver_threadpool_busy`, `_limit` and `_waiting`, and `fastapiobserver_db_pool_checked_out{engine}` and `fastapiobserver_db_pool_limit{engine}`. The check costs about 0.3 µs per request, plus 0.2 µs per database pool.

### Changed
- `db_engine` is used without tracing too, to watch connection pools. A string passed as `db_engine` now raises `TypeError` even with tracing off.

## [1.12.0] - 2026-10-31

### Security
- With FastAPI 0.142 or later, OTLP log export on and tracing off, FastAPI's own OpenTelemetry support exported validation failures and unhandled exceptions, with exception messages and stack traces, through the logger provider fastapi-observer set up. Those records skipped fastapi-observer's redaction and duplicated its `request.failed` lines. FastAPI's own log export is now turned off whenever fastapi-observer exports OTLP logs. Affected: every earlier release used with FastAPI 0.142.0 (2026-09-29) or later, `OTEL_LOGS_ENABLED=true` and `OTEL_ENABLED=false`.

### Changed
- On FastAPI 0.142 and later, request tracing uses FastAPI's own OpenTelemetry support instead of OpenTelemetry's FastAPI instrumentation. Traces gain spans for dependency resolution, the endpoint and serialization. Excluded URLs still apply. CPU added per traced request went from 187 to 175 µs in `examples/benchmarks/request_cpu.py`. Older FastAPI versions are unchanged, and `FastAPI(telemetry={"tracing": False})` keeps the instrumentation.
- A failed request's server span has `error.type` and an `ERROR` status instead of an exception event. The exception message and stack trace are in the redacted `request.failed` log line, which carries the same `trace_id`.
- With OTLP metrics on, HTTP server metrics come from FastAPI: `http.server.request.duration` (seconds) and `http.server.active_requests`. The instrumentation's `http.server.duration` (milliseconds) and `http.server.response.size` are gone. **Dashboards and alerts on those names need updating.**

### Added
- Decision record 0010, on tracing with FastAPI's own OpenTelemetry.

## [1.11.1] - 2026-10-31

### Fixed
- Metric `path` labels, `fastapiobserver_log_bytes_total` routes and `db.n_plus_one` routes keep the prefixes of routers added with `include_router(router, prefix=...)` and of outer routers. Since FastAPI 0.137, `scope["route"].path` leaves them out ([fastapi/fastapi#16051](https://github.com/fastapi/fastapi/discussions/16051)), so `/v1/items/{item_id}` and `/v2/items/{item_id}` shared the label `/items/{item_id}`. **Dashboards and alerts that filter on the shortened paths need updating.**

## [1.11.0] - 2026-10-31

### Added
- `LOG_SAMPLE_RATE`: the `INFO` and lower lines of only this share of requests are written, `request.completed` included. Requests are chosen by trace ID with the rule OpenTelemetry's `TraceIdRatioBased` sampler uses, so at the same ratio a request's lines are kept exactly when its trace is, in every service. Without a trace ID, the request ID is hashed. Failed requests, requests that take `LOG_SLOW_REQUEST_MS` (default 1000) or longer, `WARNING` and higher lines, and lines outside a request are always written. Metrics still count every request. At 0.1, CPU per request in `examples/benchmarks/request_cpu.py` went from 51 to 38 µs.
- Decision record 0009, on sampling per request by trace ID.

## [1.10.0] - 2026-10-31

### Added
- `OBS_LOOP_BLOCK_THRESHOLD_MS`: when the event loop doesn't run for this long, a background thread writes `event_loop.blocked` with the stack of the call that's blocking it, such as `requests.get` or `time.sleep` inside `async def`. One line per block. The thread runs during the app's lifespan.
- `LOG_LEVEL_ON_ERROR`: a request's lines from this level up to `LOG_LEVEL` are held in memory, at most 200, and written before its `request.failed` or `request.server_error` line only if it ends in a 5xx or an unhandled exception. With it set, the root logger runs at the lower level, so those records are created.
- With tracing on, request lines have `db` and `http` fields, `{"count", "ms"}`, for the request's database queries and outbound HTTP calls, taken from OpenTelemetry client spans. Sync endpoints included; unsampled requests have neither.
- A `db.n_plus_one` warning when one statement runs 10 or more times in a request, once per method, route and statement in each process. Quoted strings and numbers in the statement are replaced with `?`.
- `fastapiobserver_log_bytes_total{route}` on `/metrics`: bytes of JSON log lines written, by the route template of the request that wrote them. A line written to several sinks counts once.
- Decision record 0008, on the per-request state object behind these, and `examples/benchmarks/request_cpu.py`, which measures CPU per request. These features add about 2 µs per request with all of them off.

### Fixed
- `set_user_context()` called in a sync (`def`) endpoint or dependency now appears on the request's `request.completed` line. It used to be set only in the thread pool's copy of the context, which the middleware never sees.

## [1.9.0] - 2026-10-31

### Added
- Design decision records in the docs, each with its evidence and what it cost, and the two benchmark scripts behind them: `examples/benchmarks/middleware_overhead.py` and `slow_sink.py`.
- A postmortem of the unredacted OTLP log export fixed in 1.5.0. Releases 0.2.1 to 1.4.1 were affected from 2026-03-04. The upstream change came in 0.61b0, not 0.66 as the 1.5.0 entry said; that entry is corrected.

### Fixed
- 404s for paths that no route matches share one `path="<unmatched>"` metrics label instead of adding a series per URL. Request logs keep the real path. Dashboards that filter on a specific 404 path need updating.
- `METRICS_EXCLUDE_PATHS` applies when the app runs without a lifespan (`uvicorn --lifespan off`). Before, `/metrics` scrapes and health probes were counted.

## [1.8.0] - 2026-10-31

### Security
- Log sinks from installed packages load only when named in the new `OBS_LOG_SINKS` setting. Before, any installed package that declared a `fastapiobserver.log_sinks` entry point was imported at startup and received every log line. Entry points that aren't listed are never imported, and a `sinks.entry_point.not_enabled` warning names them. **If you use such a sink, add its entry-point name to `OBS_LOG_SINKS`.**
- The runtime control plane only accepts callers whose address is in `OBS_TRUSTED_CIDRS`, checked before the token. A request that arrives through a proxy (it has `X-Forwarded-For`) is refused unless `OBS_HONOR_FORWARDED_HEADERS=true` resolves it to a trusted address, so the endpoint can't be reached, or its token guessed, through a public ingress. `mount_control_plane()` takes an optional `trusted_proxy_policy`. Tests that call the endpoint through Starlette's `TestClient` need `TestClient(app, client=("127.0.0.1", 50000))`, since its default caller, `testclient`, isn't an address.
- The Logtail dead-letter directory is created with mode `0700` and its files, including compressed rotations, with `0600`. They used to follow the process umask, usually world-readable. Existing files and directories keep their permissions.

### Added
- Outbound calls carry the request ID, so a request keeps one ID across services, with or without tracing. `inject_trace_headers()` adds it as `REQUEST_ID_HEADER`, and with the `[otel]` extra `install_observability()` adds it to OpenTelemetry's global propagator, so instrumented `httpx` and `requests` clients send it next to `traceparent`. A request ID header already set on the call is kept. The next service accepts it only from `OBS_TRUSTED_CIDRS`, which by default includes private networks.
- `install_observability()` writes one `observability.configured` line at startup with the settings that took effect: profile, log level, metrics path, OpenTelemetry endpoint, protocol and sampling, OTLP logs and metrics, redaction mode, body capture, trusted networks, request ID header, control plane path and enabled sinks. Endpoint credentials, query strings and tokens are left out.

### Changed
- `instrument_httpx_client()` raises `RuntimeError` when the `httpx` package isn't installed, for example in apps that only use `httpx2`. OpenTelemetry's instrumentor used to log an error and instrument nothing. The OpenTelemetry guide shows the one-line request hook for `httpx2` and other clients.

### Fixed
- `METRICS_EXCLUDE_PATHS` set as a comma-separated list, as the configuration guide shows, no longer stops the app from starting with a `SettingsError`. A JSON array still works.

## [1.7.0] - 2026-10-31

### Fixed
- Database tracing now covers every engine. OpenTelemetry's SQLAlchemy instrumentor is a singleton that ignores a second `instrument()` call, so `install_observability(db_engine=[a, b])` or repeated `instrument_sqlalchemy()` calls traced only the first engine. All engines are now instrumented together.
- When OpenTelemetry's SQLAlchemy instrumentation refuses the installed SQLAlchemy version (it currently supports versions below 2.1), fastapi-observer logs a `db_tracing.not_instrumented` warning instead of reporting `db_tracing.instrumented`.
- The sink circuit breaker now opens when a sink fails to write. Standard library handlers catch their own write errors and only report them through `handleError()`, so the breaker counted every failed write as a success and never opened.
- OTLP metrics are exported. `install_otel_metrics()` looked for a provider class named `ProxyMeterProvider`, but OpenTelemetry's is `_ProxyMeterProvider`, so the SDK `MeterProvider` was never installed and nothing was exported.
- Logs written while the app shuts down reach the OTLP exporter. The log provider was shut down before the log queue had drained.
- With `http/protobuf`, `OTEL_LOGS_ENDPOINT` gets `/v1/logs` appended to a bare host, as trace and metric endpoints already did. Logs were posted to `/`.
- Failed requests record one exception event on the server span instead of two; OpenTelemetry's FastAPI instrumentation already records it.
- `METRICS_FORMAT=prometheus` always serves the classic text format; it used to negotiate OpenMetrics. In `prometheus` and multiprocess modes, `/metrics` no longer redirects to `/metrics/`, and the redirect target is no longer counted in the request metrics. Multiprocess mode now honours `METRICS_FORMAT`.

### Changed
- `/metrics` uses `prometheus_client`'s own format negotiation, gzip compression and `name[]` filtering instead of a hand-written copy. Rendering still runs in the thread pool, so large metric sets don't block the event loop. Differences from the old negotiation: `q=0` in `Accept` is no longer honoured, OpenMetrics responses carry `prometheus_client`'s `escaping` parameter, and media types are matched case-sensitively.
- OTLP log export uses the `LoggingHandler` from `opentelemetry-instrumentation-logging` instead of the SDK's deprecated one. Records are still redacted before export, and no handler is attached to the root logger.
- With gRPC, an `OTEL_LOGS_ENDPOINT` ending in `/v1/logs` is rejected, as trace and metric endpoints already were.
- Providers fastapi-observer creates are flushed when the app's lifespan ends; at process exit the OpenTelemetry SDK's own hook takes over. Providers configured by the application are no longer force-flushed by fastapi-observer at exit.
- An existing tracer provider that is not an SDK provider is reused instead of replaced.
- If you wrap the app in OpenTelemetry's raw ASGI middleware instead of `FastAPIInstrumentor`, failed requests no longer get an exception event from fastapi-observer; the span status is still `ERROR`.
- Internals of `otel/` and `metrics/` now lean on the OpenTelemetry SDK and `prometheus_client`; the public API is unchanged.

### Removed
- The `route` SQLCommenter option. OpenTelemetry's SQLCommenter never supported it, so it had no effect; the docs no longer show it.

## [1.6.0] - 2026-10-31

### Added
- `StructuredJsonFormatter()` without arguments reads its settings and security policy from the environment, like `install_observability()`. Server log configs (gunicorn's `logconfig_dict`, Granian's `--log-config`) can now build it by name, so the servers' own startup and worker lines come out as the same JSON.
- Documentation pages for running with uvicorn, gunicorn and Granian, sending data to backends, and using fastapi-observer alongside Sentry.
- `SECURITY.md` with the private reporting process, and GitHub issue templates.

### Changed
- README rewritten: the one-call quick start and a real log line come first, followed by a comparison with structlog, loguru, OpenTelemetry and Logfire, and when not to use the library. The docs home page is generated from it.
- `NEXT_STEPS.md` is now `ROADMAP.md`, listing only open work. The scratch files `main.py` and `test_middleware.py` were removed from the repository root.

## [1.5.0] - 2026-10-31

### Security
- With `honor_forwarded_headers` on (`OBS_HONOR_FORWARDED_HEADERS=true`, off by default), the logged `client_ip` was the leftmost `X-Forwarded-For` entry, which the client controls: proxies such as nginx (`$proxy_add_x_forwarded_for`), AWS ALB and Traefik append to the header the client sent. Clients could log any IP they chose. The client IP is now the rightmost address that is not in `trusted_cidrs`, and repeated `X-Forwarded-For` lines are read as one list. Request ID and `traceparent` trust were not affected; they check the direct peer.

### Fixed
- With tracing enabled, the library no longer calls OpenTelemetry's `LoggingInstrumentor`. Since `opentelemetry-instrumentation-logging` 0.61 it attaches its own handler to the root logger, which bypassed the queue and redaction: with OTLP logs enabled, every record was exported a second time without redaction.
- Exceptions logged through the queue keep the structured `error` field (type, message, stack trace, fingerprint). The stdlib `QueueHandler.prepare()` dropped `exc_info` and appended the traceback to `message`.
- `user_context` set with `set_user_context()` now appears in JSON logs. It was read on the queue listener thread, where the request's context variables are not visible.
- The log `timestamp` is the time the record was created, not the time the background thread formatted it.

### Changed
- `install_observability()` drops uvicorn's access log line, as `uvicorn --no-access-log` does. The middleware's `request.completed` line carries the same fields, so every request was logged twice. To keep uvicorn's line, set `logging.getLogger("uvicorn.access").propagate = True` after `install_observability()`.
- Lower per-request overhead. Redaction builds its key sets once per policy instead of once per record and skips scalar values sooner. The request event is not built when the log level would drop it. Without the `otel` extra, the trace-context filter no longer retries a failing `import opentelemetry` on every record. Metric hooks no longer build a `Request` and `Response` when none are registered. Body-capture wrappers, metrics bookkeeping, log filters and enrichers are skipped when they are off or empty. Together with dropping the duplicate access line, throughput in the benchmark with default settings went from 3,541 to 4,393 requests/s.
- The benchmark harness uses ApacheBench (`ab`) instead of `hey`, takes scenario names as arguments, needs Docker only for tracing scenarios, and adds a hand-wired comparison scenario (`H4`). `docs/benchmarks.md` was rewritten with the new results.
- `requires-python` no longer has an upper bound (was `<3.16`). With a cap, installers on a newer Python fall back to an old release without one instead of the current version.
- Development and CI tooling run on Python 3.14: `.python-version`, the CI quality job and the runtime stress workflow (previously 3.12 or 3.13). The test matrix covers 3.11 to 3.15.

### Removed
- Python 3.10 support. Python 3.10 reached end of life in October 2026; on 3.10, installers keep resolving fastapi-observer 1.4.1.

## [1.4.1] - 2026-10-31

### Added
- Documentation site at https://vitaee.github.io/FastapiObserver/, built from `docs/` with Zensical and deployed to GitHub Pages by a new `Docs` workflow (strict build on pull requests that touch the docs).

### Fixed
- README documentation links are now absolute links to the docs site. The relative `docs/*.md` links returned 404 on the PyPI project page, and the `Documentation` project URL now points to the site.

## [1.4.0] - 2026-10-31

### Added
- Python 3.15 support (`requires-python` now `<3.16`, added to the CI matrix).

### Changed
- Raised minimum versions past releases with known vulnerabilities: `starlette>=1.3.1` (and `fastapi>=0.133.0`, the first release allowing Starlette 1.x), `opentelemetry-*>=1.45.1` / instrumentations `>=0.66b1`, and `cryptography>=50.0.0` for the `audit` extra.
- Refreshed `uv.lock` to the latest releases and raised dev tooling minimums (mypy 2.x, ruff 0.17, pytest 9.2, pytest-randomly 5). Dev tests use `httpx2` on Python 3.11+, as Starlette's test client now expects.
- Updated GitHub Actions to `actions/checkout@v7`, `actions/setup-python@v7` and `softprops/action-gh-release@v3`.

### Removed
- Unused `mkdocs-material` dependency group (the repository has no MkDocs config).

### Fixed
- Starlette 1.0 compatibility: OTel provider shutdown hooks now wrap the app lifespan instead of calling the removed `add_event_handler`, which crashed `install_observability` at startup when OTel was enabled on FastAPI with Starlette 1.x. Hooks now also run when the app defines its own `lifespan`.
- `sanitize_event` now masks containers nested beyond `MAX_SANITIZE_DEPTH` instead of returning them unredacted, and the limit now also applies to nested lists and tuples.

## [1.3.2] - 2026-02-27

### Fixed
- Request metrics exclusions now always include the configured `metrics_path`, so custom scrape endpoints are not recorded as application traffic.
- Logging queue listener fork reinitialization now rewires queue handlers correctly in child processes and avoids dead listener queue buildup after `fork()`.
- Route auto-discovery no longer raises startup errors when a middleware named `OpenTelemetryMiddleware` is present but `opentelemetry.util.http` is unavailable.
- Auto lifespan wrapper now reuses `observability_lifespan` to avoid duplicated setup/teardown logic.

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
