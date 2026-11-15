# Structured Logging Guide

`fastapi-observer` emits structured request and WebSocket diagnostics and provides an optional
non-blocking JSON logging pipeline. By default it configures logging; established applications
can keep their own pipeline with `configure_logging=False`.

## Application-owned logging

Configure your application logger first, then install diagnostics:

```python
from fastapiobserver import install_observability

# Your existing logging configuration, including your redaction, is in place.
install_observability(app, configure_logging=False)
```

This option preserves root, application and Uvicorn handlers, filters, formatters,
levels and propagation settings. It adds no output handler, queue, log processor
or log exporter. Each diagnostic follows the application's existing routing and
level rules. Uvicorn access logs stay enabled when your application has enabled them.

The `fastapiobserver.middleware` logger captures `request_id`, `user_context` and
available `trace_id` / `span_id` before existing logger filters and handlers run.
This also works with application-owned queues: formatting on another thread
does not lose the request context. Request fields remain on `LogRecord.event`;
your formatter must serialize `event` and the correlation attributes to include
them in output.

The application owns output sanitization, including free text, captured context
and exceptions. Body-capture controls and captured-body masking still apply,
but `SecurityPolicy` does not sanitize the application's output handlers.
`OBS_LOG_*` pipeline controls (levels, sampling, held logs, files, queues and
sinks), registered log filters/enrichers and `otel_logs_settings` do not configure
the application pipeline. Runtime-control requests to change log levels return `409`.
Request metrics, database/HTTP diagnostics, the event-loop watchdog and optional
tracing and metric export remain available; see
[OpenTelemetry ownership](opentelemetry.md#application-owned-logging-and-opentelemetry).

For correlation on your application's own records, you can attach `RequestIdFilter`
and `TraceContextFilter` from `fastapiobserver.logging` to your producer-side handlers.
See [`examples/existing_logging_app.py`](https://github.com/Vitaee/FastapiObserver/blob/main/examples/existing_logging_app.py)
for a runnable stdlib JSON integration. The rest of this page describes the default
library-owned pipeline.

## What Gets Logged on Requests and WebSockets

Request middleware emits one primary event per HTTP request or WebSocket session:

| Message | Level | Trigger |
|---|---|---|
| `request.completed` | `INFO` | Successful HTTP response (`< 400`) |
| `request.client_error` | `WARNING` | Client-side HTTP errors (`4xx`) |
| `request.server_error` | `ERROR` | Server-side HTTP errors (`5xx` without exception) |
| `request.failed` | `ERROR` + traceback | Unhandled exception in HTTP request path |
| `request.cancelled` | `WARNING` | Cancelled before completion (`status_code` is `499` if no response started) |
| `request.client_disconnected` | `WARNING` | Application received `http.disconnect` before response completion and ran on (`after_disconnect_ms` and `after_disconnect_calls` report wasted work). See [servers](servers.md) |
| `websocket.closed` | `INFO` | Accepted WebSocket closed cleanly (`close_code`, `messages_sent`, `messages_received`, `bytes_sent`, `bytes_received`) |
| `websocket.rejected` | `WARNING` | WebSocket closed before `websocket.accept` |
| `websocket.failed` | `ERROR` + traceback | Unhandled exception during WebSocket session |

Standard request event fields include:
- `method`, `path`, `status_code`, `client_ip`, `error_type`, and OTel aliases (`http.request.method`, `url.path`, `http.response.status_code`)
- `http.route`: the matched route template, omitted when no route matched
- `duration_ms` and `app_duration_ms`
- `response_start_ms`, `first_body_ms`, `stream_duration_ms`, and `response_bytes` (total response body bytes sent). See [route and timing](request-diagnostics.md#route-and-timing)
- `response_completed`, `cancelled`, `client_disconnected`, `after_disconnect_ms` and `after_disconnect_calls`. See [client disconnects](servers.md#client-disconnects-and-shutdown)
- `diagnostics` and, on `422` responses, `validation_errors`. See [per-request diagnostics](request-diagnostics.md#per-request-diagnostics-and-422-validation-errors)
- `observation` and `log_sampling`: see [observation coverage](request-diagnostics.md#observation-coverage) and [sampling policy](request-diagnostics.md#sampling-policy-and-delivery)
- `db` and `http`: count and total duration of completed database queries and outbound HTTP calls. See [database tracing](db-tracing.md#query-count-and-time-on-the-request-line) and [HTTP diagnostics](opentelemetry.md#outbound-http-diagnostics).

### What the durations mean

`duration_ms` and the HTTP duration histogram start at middleware entry and stop
after the final response body has been passed to ASGI `send` (or after final
trailers). They include handler work, serialization and streaming, and exclude
work performed after the response.

`app_duration_ms` stops when the ASGI application returns or raises, including
background tasks and dependency cleanup. The request line is written then, while
HTTP metrics are recorded once when the response finishes.

`response_completed=false` means no final send was observed (`duration_ms`
measures time until interruption). Database and HTTP totals cover work completed
before the application ends, including background tasks, so their summed durations
can exceed `duration_ms` when calls overlap or run after the response.

## Debug logs for failed requests

Set `OBS_LOG_LEVEL_ON_ERROR` below `OBS_LOG_LEVEL` to buffer a request's lower-level lines in memory while it runs:

```bash
OBS_LOG_LEVEL=INFO
OBS_LOG_LEVEL_ON_ERROR=DEBUG
```

- If the request ends in a `5xx` or an unhandled exception, up to the last 200 held lines are flushed just before `request.failed` or `request.server_error` with the same `request_id` and `trace_id`.
- Otherwise they are dropped without formatting or queue overhead.
- A level set through the [runtime control plane](runtime-control.md) replaces `OBS_LOG_LEVEL`, and holding stays active.

## Sampling request logs

To sample successful `INFO` requests while retaining all warnings, errors, and slow requests:

```bash
OBS_LOG_SAMPLE_RATE=0.1         # Keep 10% of fast 2xx/3xx request logs
OBS_LOG_SLOW_REQUEST_MS=1000    # Always keep requests >= 1000 ms
```

- A request is kept or dropped as a whole, including its terminal `request.completed` line. Unsampled `INFO` requests skip terminal event dictionary construction and sanitization entirely.
- Any request that fails (`5xx` or unhandled exception), emits `WARNING`+, or takes `OBS_LOG_SLOW_REQUEST_MS` or longer is written in full.
- Prometheus metrics still count every request.
- Decisions use OpenTelemetry's `TraceIdRatioBased` rule when a `trace_id` is present, or a hash of `request_id` otherwise, keeping whole requests aligned across services.

## Calls that block the event loop

A synchronous call inside `async def` (`requests.get`, `time.sleep`, sync DB driver) stalls every concurrent request on that worker. Enable the watchdog via the `production` profile (`250 ms`) or explicitly:

```bash
OBS_LOOP_BLOCK_THRESHOLD_MS=200
```

When the loop stalls past the threshold, the watchdog thread logs one warning per block with the offending call stack and tags in-flight requests with `"event_loop.blocked"` in their `diagnostics` array:

```json
{"level": "WARNING", "logger": "fastapiobserver.event_loop", "message": "event_loop.blocked", "event": {"blocked_ms": 212, "stack": "...  File \"/app/orders.py\", line 41, in get_order\n    rates = requests.get(RATES_URL).json()\n"}}
```

## Full thread and database pools

Two pools make requests wait without using CPU:
- AnyIO's thread pool runs sync (`def`) endpoints and dependencies (40 threads by default).
- SQLAlchemy's `QueuePool` hands out at most `pool_size + max_overflow` connections.

`fastapi-observer` checks both at request entry, tags affected requests in `event.diagnostics`, and emits a rate-limited warning (at most once per minute per pool):

```json
{"level": "WARNING", "logger": "fastapiobserver.pools", "message": "threadpool.saturated", "event": {"busy": 40, "limit": 40, "waiting": 12}}
{"level": "WARNING", "logger": "fastapiobserver.pools", "message": "db.pool_exhausted", "event": {"engine": "postgresql://app@db/orders", "checked_out": 15, "limit": 15}}
```

The pool gauges and counters on `/metrics` are listed under [metrics](advanced.md#metrics).

## JSON Schema Contract

| Field | Purpose |
|---|---|
| `timestamp`, `level`, `logger`, `message` | UTC time, severity, logger name, and event name |
| `app_name`, `service`, `environment`, `version` | Service identity |
| `log_schema_version`, `library`, `library_version` | Schema and producer identity |
| `request_id`, `trace_id`, `span_id`, `user_context` | Correlation and plugin context |
| `event` | Structured request/diagnostic payload |
| `error` | Structured exception payload (`type`, `message`, `stacktrace`, `fingerprint`) |

Exception payloads include `error.fingerprint` (a 128-bit digest of the exception type and normalized call frames, excluding dynamic message text and line numbers) and automatically strip credentials from URLs in `error.message` and `error.stacktrace` (`postgresql://***:***@db/app`).

## Output, Queue, and Shutdown Behavior

- Local JSON logs write to `stdout` by default; set `OBS_LOG_DIR` for rotating file logs, or `logs_mode` (`local_json`, `otlp`, `both`) when OTel logs are enabled.
- Uvicorn logs route through the same queue, and duplicate `uvicorn.access` lines are suppressed automatically.
- The bounded in-memory queue supports `drop_oldest` (default), `drop_newest`, and `block`, with per-sink circuit breakers. See [bounded queue](advanced.md#bounded-queue-and-overflow-policy) and [sink circuit breaker](advanced.md#sink-circuit-breaker).
- `flush_logging(timeout_seconds=5.0)` and `shutdown_logging()` drain queued records cleanly when the FastAPI lifespan ends or at process exit (`atexit`).
