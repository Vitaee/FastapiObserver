# Structured Logging Guide

fastapi-observer emits structured request diagnostics and provides an optional
JSON logging pipeline. By default it configures logging; established applications
can keep their own pipeline with `configure_logging=False`.

## Application-owned logging

Configure your application logger first, then install diagnostics:

```python
from fastapiobserver import install_observability

# Your existing logging configuration, including approved redaction, is in place.
install_observability(app, configure_logging=False)
```

This option preserves root, application and Uvicorn handlers, filters, formatters,
levels and propagation settings. It adds no output handler, queue, log processor
or log exporter. Each diagnostic follows the application's existing routing and
level rules. If that routing already delivers a record twice, it still will.
Uvicorn access logs stay enabled when your application has enabled them.

The `fastapiobserver.middleware` logger captures `request_id`, `user_context` and
available `trace_id` / `span_id` before existing logger filters and handlers run.
This also works with application-owned queues: formatting on another thread
does not lose the request context. Existing filters on this library logger are
retained, with context capture placed before them. Request fields remain on
`LogRecord.event`; your formatter must serialize `event` and the correlation
attributes to include them in output. Application log levels still decide
whether a diagnostic is emitted.

The application owns output sanitization, including free text, captured context
and exceptions. Body-capture controls and captured-body masking still apply,
but `SecurityPolicy` does not sanitize the application's output handlers.
Keep the approved sanitizer on every output path, including log export. A filter
on the root *logger* alone does not filter records propagated by child loggers;
attach it to the handlers that write or export them.

`OBS_LOG_*` pipeline controls (levels, sampling, held logs, files, queues and
sinks), registered log filters/enrichers and `otel_logs_settings` do not configure
the application pipeline. Runtime-control requests to change log levels return
`409`. Request metrics, database/HTTP diagnostics, the event-loop watchdog and
optional tracing and metric export remain available. The application drains and
shuts down its own logging resources; the library does not flush or close them.

Existing OpenTelemetry providers stay in place. This mode creates no additional
OTel logging handler or exporting log processor, even when `OBS_OTEL_LOGS_ENABLED`
is set. Tracing and metric options remain independent; see
[OpenTelemetry ownership](opentelemetry.md#application-owned-logging-and-opentelemetry).

For correlation on your application's own records, keep your existing context
integration. You can also explicitly add `RequestIdFilter` and `TraceContextFilter`
from `fastapiobserver.logging` to your application's producer-side handlers.
The installer does not add them to application handlers.

Run the [existing-logger example](https://github.com/Vitaee/FastapiObserver/blob/main/examples/existing_logging_app.py):

```bash
uv run uvicorn examples.existing_logging_app:app
# In another terminal:
curl http://localhost:8000/orders/42
curl http://localhost:8000/failure
```

It uses a stdlib JSON formatter and an application redaction filter, with
synthetic email addresses and tokens in messages, structured fields and errors.
Its small sanitizer illustrates the integration; substitute your organisation's
approved sanitizer and retain its existing test vectors. The real-server test
checks concurrent correlation, failure redaction and one application event per
request. The rest of this page describes the default library-owned pipeline.

## Why Use This Logging Model

Traditional text logs are hard to query, hard to correlate across services, and
fragile under load. This pipeline is built for production observability:

| Benefit | What you get |
|---|---|
| Queryable logs | Stable JSON schema for dashboards, alerts, and SIEM queries |
| Correlation by default | `request_id`, `trace_id`, and `span_id` attached when available |
| Field-based sanitization | Security policy masking before library sink output; [free-text limits](security.md#what-redaction-covers) apply |
| Backpressure controls | Bounded queue with explicit overflow policy |
| Sink isolation | Circuit breaker protects request path from failing log sinks |

## What Gets Logged on Requests

Request middleware emits one primary event per request:

| Message | Level | Trigger |
|---|---|---|
| `request.completed` | `INFO` | Successful response (`< 400`) |
| `request.client_error` | `WARNING` | Client-side HTTP errors (`4xx`) |
| `request.server_error` | `ERROR` | Server-side HTTP errors (`5xx` without exception) |
| `request.failed` | `ERROR` + traceback | Unhandled exception in request path |
| `request.cancelled` | `WARNING` | Cancelled before it finished: the client left, the server shut down or a timeout fired. `status_code` is `499` if no response had started |

Standard request event fields include:
- `method`
- `path`
- `status_code`
- `duration_ms`
- `app_duration_ms`
- `response_completed`
- `client_ip`
- `error_type`
- OTel semantic aliases (`http.request.method`, `url.path`, `http.response.status_code`)
- `db` and `http`: the count and total time of completed database queries and outbound HTTP calls. SQLAlchemy engines passed as `db_engine`, HTTPX/HTTPX2 clients passed to `instrument_httpx_client()`, and Requests instrumented with `instrument_requests_session()` are counted without tracing. Other instrumented clients are counted from sampled spans. See [database tracing](db-tracing.md#query-count-and-time-on-the-request-line) and [HTTP diagnostics](opentelemetry.md#outbound-http-diagnostics).

### What the durations mean

`duration_ms` and the HTTP duration histogram start at middleware entry and stop
after the final response body has been passed successfully to ASGI `send`, or
after final trailers when the response declares them. They include handler work,
response serialization and streaming, and exclude work performed after the response.
This measures the application's response send, not the time a remote client receives it.

`app_duration_ms` stops when the ASGI application returns or raises. It includes
background tasks and dependency cleanup. The request line is written then, so
later failures retain their request ID and exception information. HTTP metrics
are recorded once when the response finishes, even while background work continues.

`response_completed=false` means no successful final send was observed. In that
case `duration_ms` measures time until interruption. An exception before headers
uses status 500; once headers are sent, the logged and measured status is kept.
For example, background work can produce `request.failed`, status 202 and
`response_completed=true` without changing the completed response's metrics.

Database and HTTP totals cover work completed before the application ends,
including background work. Their summed durations can exceed `duration_ms` when
calls overlap or run after the response. The slow-request log retention threshold
uses `app_duration_ms`, so slow background work can retain diagnostic logs too.

## Debug logs for failed requests

Set `OBS_LOG_LEVEL_ON_ERROR` below `OBS_LOG_LEVEL` to keep a request's lower-level lines in memory while it runs:

```bash
OBS_LOG_LEVEL=INFO
OBS_LOG_LEVEL_ON_ERROR=DEBUG
```

- If the request ends in a 5xx or an unhandled exception, its held lines are written just before its `request.failed` or `request.server_error` line, with the same `request_id` and `trace_id`.
- Otherwise they're dropped, as `OBS_LOG_LEVEL` would have dropped them.
- The last 200 lines of a request are kept.
- Lines below `OBS_LOG_LEVEL` outside a request are dropped.
- A level set through the [runtime control plane](runtime-control.md) replaces `OBS_LOG_LEVEL`, and holding stays on.

What it costs: the root logger is set to the lower level, so every `DEBUG` call in the process creates a record, and other handlers on the root logger receive them. Loggers you set below `OBS_LOG_LEVEL` yourself are held too.

## Sampling request logs

Successful requests write most log lines. To keep a share of them:

```bash
OBS_LOG_SAMPLE_RATE=0.1         # INFO and lower lines of 10% of requests
OBS_LOG_SLOW_REQUEST_MS=1000    # the default
```

- A request is kept or dropped as a whole, its `request.completed` line included.
- A request that fails (a 5xx or an unhandled exception) or takes `OBS_LOG_SLOW_REQUEST_MS` or longer is always written in full.
- `WARNING` and higher lines are always written, `request.client_error` included. Lines outside a request aren't sampled.
- Metrics still count every request.

Requests are chosen by trace ID, with the rule OpenTelemetry's `TraceIdRatioBased` sampler uses for `OTEL_TRACES_SAMPLER_ARG`. If every service samples traces that way at the same ratio, a request's lines are written exactly when its trace is kept, in every service. You get whole requests across services, not scattered lines. A lower log ratio keeps a subset of the traced requests. Without a trace ID, the request ID is hashed instead. Services forward it, so they still agree.

The lines of a request that isn't sampled are held until it ends, at most the last 200. In [`request_cpu.py`](https://github.com/Vitaee/FastapiObserver/blob/main/examples/benchmarks/request_cpu.py), `OBS_LOG_SAMPLE_RATE=0.1` took CPU per request from 45 to 37 µs.

## Calls that block the event loop

A sync call inside `async def`, such as `requests.get`, `time.sleep` or a sync database driver, stops every request on the worker until it returns. The `production` profile watches for them at 250 ms. Without it, set a threshold:

```bash
OBS_LOOP_BLOCK_THRESHOLD_MS=200
```

A background thread checks that the event loop keeps running. When it hasn't run for the threshold, the thread writes one warning per block, with the stack of the call that's blocking it:

```json
{"level": "WARNING", "logger": "fastapiobserver.event_loop", "message": "event_loop.blocked", "event": {"blocked_ms": 212, "stack": "...  File \"/app/orders.py\", line 41, in get_order\n    rates = requests.get(RATES_URL).json()\n"}}
```

- `blocked_ms` is how long the loop had been blocked when the stack was taken. The block can last longer.
- To fix one, make the endpoint a plain `def`, which FastAPI runs in its thread pool, wrap the call in `await asyncio.to_thread(...)`, or use an async client.
- The thread runs during the app's lifespan, so not with `uvicorn --lifespan off`.
- Under heavy CPU load the loop can fall behind without one blocking call. The stack is then whatever was running at that moment.
- It costs nothing measurable per request, and about 0.25% of one core per worker while idle. See [decision record 0014](decisions/0014-diagnosis-without-tracing.md).

## Full thread and database pools

Two pools make requests wait without using CPU, so latency climbs while CPU stays low:

- AnyIO's thread pool runs sync (`def`) endpoints and dependencies, 40 threads at a time by default. When all are busy, the next sync call waits.
- A SQLAlchemy `QueuePool` hands out at most `pool_size + max_overflow` connections. When all are checked out, the next session waits up to `pool_timeout` (30 s by default), then fails with `QueuePool limit ... reached`.

fastapi-observer checks both as each request starts. While a pool is full, it writes a warning at most once a minute for that pool. For example:

```json
{"level": "WARNING", "logger": "fastapiobserver.pools", "message": "threadpool.saturated", "event": {"busy": 40, "limit": 40, "waiting": 12}}
{"level": "WARNING", "logger": "fastapiobserver.pools", "message": "db.pool_exhausted", "event": {"engine": "postgresql://app@db/orders", "checked_out": 15, "limit": 15}}
```

New requests keep starting while every thread and connection is stuck, so a deadlock is reported too. Both warnings together match the deadlock in [fastapi/fastapi#6628](https://github.com/fastapi/fastapi/discussions/6628). Sync endpoints take every thread while they wait for a connection, and the sync dependencies that would close the open sessions need a thread to run.

- The thread pool is watched once the app's lifespan starts.
- Database pools are watched for the engines passed as `install_observability(app, db_engine=engine)`, with or without tracing. Pools without a limit, such as `NullPool`, are skipped.
- With metrics on, `/metrics` shows the same numbers as gauges: `fastapiobserver_threadpool_busy`, `fastapiobserver_threadpool_limit`, `fastapiobserver_threadpool_waiting`, `fastapiobserver_db_pool_checked_out{engine}` and `fastapiobserver_db_pool_limit{engine}`. In Prometheus multiprocess mode these gauges aren't exported, because they describe one worker; the warnings still appear in each worker's logs.
- Neither AnyIO nor SQLAlchemy reports how long one call waited, so there's no wait time per request.

## JSON Schema Contract

Top-level fields:

| Field | Purpose |
|---|---|
| `timestamp` | UTC event time |
| `level` | Log severity |
| `logger` | Logger name |
| `message` | Event name |
| `app_name` / `service` / `environment` / `version` | Service identity |
| `log_schema_version` | Schema compatibility marker |
| `library` / `library_version` | Producer identity |
| `request_id` | Request correlation ID (when in request context) |
| `trace_id` / `span_id` | Trace correlation (when trace context exists) |
| `user_context` | Optional contextual fields (plugins/integrations) |
| `event` | Structured request/event payload |
| `error` | Structured exception payload (only on exception logs) |

Example request log:

```json
{
  "timestamp": "2026-02-18T10:30:00.000000+00:00",
  "level": "INFO",
  "logger": "fastapiobserver.middleware",
  "message": "request.completed",
  "app_name": "orders-api",
  "service": "orders",
  "environment": "production",
  "version": "0.1.0",
  "log_schema_version": "1.0.0",
  "library": "fastapiobserver",
  "library_version": "2.0.0",
  "request_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "trace_id": "0af7651916cd43dd8448eb211c80319c",
  "span_id": "b7ad6b7169203331",
  "event": {
    "method": "GET",
    "path": "/orders/42",
    "status_code": 200,
    "http.request.method": "GET",
    "url.path": "/orders/42",
    "http.response.status_code": 200,
    "duration_ms": 3.456,
    "client_ip": "10.0.0.1",
    "error_type": "ok"
  }
}
```

Exception payload:

```json
{
  "error": {
    "type": "RuntimeError",
    "message": "boom",
    "stacktrace": "Traceback (most recent call last): ...",
    "fingerprint": "a1b2c3d4e5f67890abcd12345678bbcc"
  }
}
```

`error.fingerprint` groups recurring failures: it hashes the exception type and the file,
function and source line of each frame. The message and line numbers are left out, so
`user 1 not found` and `user 2 not found` from the same place share one fingerprint.

The user and password of any URL in `error.message` and `error.stacktrace` are masked, so a
connection error reads `postgresql://***:***@db/app`. OTLP-exported logs carry the same
masked text in their `exception.message` and `exception.stacktrace` attributes.

## Minimal Setup

```python
from fastapi import FastAPI
from fastapiobserver import ObservabilitySettings, install_observability

app = FastAPI()
settings = ObservabilitySettings(
    app_name="orders-api",
    service="orders",
    environment="production",
)

install_observability(app, settings)
```

## Output and Pipeline Behavior

- Local JSON logs go to `stdout` by default.
- Set `OBS_LOG_DIR` to also enable rotating file logs.
- With OTel logs enabled, `logs_mode` can be `local_json`, `otlp`, or `both`.
- Uvicorn loggers are routed into the same structured pipeline. `install_observability()` drops uvicorn's access log line, the same way `uvicorn --no-access-log` does, because `request.completed` carries the same fields. To keep it anyway, set `logging.getLogger("uvicorn.access").propagate = True` after `install_observability()`.
- Queued logs are written out when the FastAPI lifespan ends; logging stops at process exit (`atexit`).

## Queue and Sink Resilience Controls

The logging pipeline uses a bounded in-memory queue and configurable overflow policy:
- `drop_oldest` (default): preserve newest traffic signal under pressure.
- `drop_newest`: preserve older queued records.
- `block`: wait for queue space up to timeout, then drop newest.

A circuit breaker skips a sink after repeated errors. All destinations share one
listener, so a slow or indefinitely blocked sink can still delay the others and
cause counted queue drops. Set transport timeouts on network sinks.

The settings are in the [configuration guide](configuration.md#identity-and-logging). The queue and breaker metrics are listed under [bounded queue](advanced.md#bounded-queue-and-overflow-policy) and [sink circuit breaker](advanced.md#sink-circuit-breaker). Why the queue drops instead of blocking is in [decision 0002](decisions/0002-logs-leave-through-a-queue.md).

`shutdown_logging()` and forced logging reconfiguration stop accepting new records
and wait up to five seconds for the listener to drain and stop. A timeout retains
the listener and its outputs for a later retry; reconfiguration fails instead of
starting overlapping writers. A handler still in use is never closed.

`flush_logging(timeout_seconds=5.0)` returns `True` after preceding records and
output flush hooks complete, or `False` on timeout/flush failure. Flush hooks run
on the listener thread. Application-owned logging keeps its own shutdown policy.
Python cannot terminate an arbitrary blocked handler thread: custom close hooks
must be bounded, and the process supervisor remains responsible for a final
termination deadline. Records in process memory are not durable.

`db.n_plus_one` includes `statement_fingerprint`, a SHA-256 digest of the complete
statement, instead of SQL text. Use parameterized statements for stable grouping.
Only the first 128 distinct fingerprints per request are tracked, and the oldest
warning keys are evicted after 1,024 entries per middleware instance. Query totals
are always counted, including queries omitted from the N+1 diagnostic budget.

## Security and Data Hygiene

- Event payloads are sanitized by `SecurityPolicy` before sink output.
- Body logging is disabled by default.
- Logged request path excludes query string.
- Header and event allowlists can enforce strict "log-only-approved-fields" behavior.

See:
- `security.md`
- `configuration.md`

## Verification Checklist

1. Start app and send a request:

```bash
curl -i http://localhost:8000/orders/42
```

2. Confirm response includes request ID header (`x-request-id` by default).
3. Confirm logs include `message=request.completed`, `request_id`, and `event.status_code`.
4. Trigger a failing endpoint and confirm:
- `message=request.failed`
- `error.type`, `error.stacktrace`, `error.fingerprint`

5. Under load, inspect queue/sink metrics on `/metrics` (with Prometheus on, in a single process).

## Production Best Practices

1. Keep one logging pipeline per process; avoid dual independent logger stacks.
2. Set `OBS_LOG_LEVEL=INFO` in production and raise temporarily via runtime control during incidents.
3. Keep request/response body capture off unless explicitly needed and risk-reviewed.
4. Choose `drop_oldest` for latency-sensitive APIs; use `block` only when loss tolerance is lower than latency tolerance.
5. Keep sink circuit breakers enabled so external sink outages do not cascade into request handling.
6. Alert on queue drops and sink breaker opens as early warning signals.

## Troubleshooting Quick Map

| Symptom | Check first |
|---|---|
| No JSON logs | Confirm `install_observability()` is called during app startup |
| Missing trace IDs | Confirm OTel tracing is enabled and spans are active |
| Missing request IDs | Confirm request path is handled by FastAPI HTTP middleware |
| Log drops under load | Check queue metrics and overflow policy |
| Sink failures | Check circuit-breaker metrics and sink-specific credentials/network |

---
