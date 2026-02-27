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

Standard request event fields include:
- `method`
- `path`
- `status_code`
- `duration_ms`
- `client_ip`
- `error_type`
- OTel semantic aliases (`http.request.method`, `url.path`, `http.response.status_code`)

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
  "library_version": "1.3.0",
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

`error.fingerprint` is stable for grouping recurring failures because transient noise
(for example memory addresses and exact line numbers) is normalized before hashing.

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
- Set `LOG_DIR` to also enable rotating file logs.
- With OTel logs enabled, `logs_mode` can be `local_json`, `otlp`, or `both`.
- Uvicorn loggers are routed into the same structured pipeline.
- Logging shutdown is handled on FastAPI lifespan shutdown and `atexit`.

## Queue and Sink Resilience Controls

The logging pipeline uses a bounded in-memory queue and configurable overflow policy:
- `drop_oldest` (default): preserve newest traffic signal under pressure.
- `drop_newest`: preserve older queued records.
- `block`: wait for queue space up to timeout, then drop newest.

Relevant settings:
- `LOG_QUEUE_MAX_SIZE`
- `LOG_QUEUE_OVERFLOW_POLICY`
- `LOG_QUEUE_BLOCK_TIMEOUT_SECONDS`
- `LOG_SINK_CIRCUIT_BREAKER_ENABLED`
- `LOG_SINK_CIRCUIT_BREAKER_FAILURE_THRESHOLD`
- `LOG_SINK_CIRCUIT_BREAKER_RECOVERY_TIMEOUT_SECONDS`

Prometheus queue/sink health metrics:
- `fastapiobserver_log_queue_size`
- `fastapiobserver_log_queue_capacity`
- `fastapiobserver_log_queue_overflow_policy_info`
- `fastapiobserver_log_queue_enqueued_total`
- `fastapiobserver_log_queue_dropped_total`
- `fastapiobserver_log_queue_blocked_total`
- `fastapiobserver_log_queue_block_timeouts_total`
- `fastapiobserver_sink_circuit_breaker_state_info`
- `fastapiobserver_sink_circuit_breaker_failures_total`
- `fastapiobserver_sink_circuit_breaker_skipped_total`

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
2. Set `LOG_LEVEL=INFO` in production and raise temporarily via runtime control during incidents.
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
