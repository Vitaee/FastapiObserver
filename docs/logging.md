# Structured Logging Guide

`fastapi-observer` installs a single structured JSON logging pipeline for FastAPI
and emits request events that are correlation-ready by default.

## Why Use This Logging Model

Traditional text logs are hard to query, hard to correlate across services, and
fragile under load. This pipeline is built for production observability:

| Benefit | What you get |
|---|---|
| Queryable logs | Stable JSON schema for dashboards, alerts, and SIEM queries |
| Correlation by default | `request_id`, `trace_id`, and `span_id` attached when available |
| Safe-by-default payloads | Security policy sanitization before sink output |
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
- `client_ip`
- `error_type`
- OTel semantic aliases (`http.request.method`, `url.path`, `http.response.status_code`)
- `db` and `http`: the count and total time of the request's database queries and outbound HTTP calls, with tracing on and the clients instrumented. See [database tracing](db-tracing.md#query-count-and-time-on-the-request-line)

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

The lines of a request that isn't sampled are held until it ends, at most the last 200. In [`request_cpu.py`](https://github.com/Vitaee/FastapiObserver/blob/main/examples/benchmarks/request_cpu.py), `OBS_LOG_SAMPLE_RATE=0.1` took CPU per request from 51 to 38 µs.

## Calls that block the event loop

A sync call inside `async def`, such as `requests.get`, `time.sleep` or a sync database driver, stops every request on the worker until it returns. Set a threshold to find them:

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
- With metrics on, `/metrics` shows the same numbers as gauges: `fastapiobserver_threadpool_busy`, `fastapiobserver_threadpool_limit`, `fastapiobserver_threadpool_waiting`, `fastapiobserver_db_pool_checked_out{engine}` and `fastapiobserver_db_pool_limit{engine}`.
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
  "library_version": "1.3.1",
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
- Logging shutdown is handled on FastAPI lifespan shutdown and `atexit`.

## Queue and Sink Resilience Controls

The logging pipeline uses a bounded in-memory queue and configurable overflow policy:
- `drop_oldest` (default): preserve newest traffic signal under pressure.
- `drop_newest`: preserve older queued records.
- `block`: wait for queue space up to timeout, then drop newest.

A circuit breaker around each sink stops a failing sink from slowing the others.

The settings are in the [configuration guide](configuration.md#identity-and-logging). The queue and breaker metrics are listed under [bounded queue](advanced.md#bounded-queue-and-overflow-policy) and [sink circuit breaker](advanced.md#sink-circuit-breaker). Why the queue drops instead of blocking is in [decision 0002](decisions/0002-logs-leave-through-a-queue.md).

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

5. Under load, inspect queue/sink metrics on `/metrics` (if Prometheus is enabled).

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
