# Database Tracing and SQLCommenter (SQLAlchemy)

This guide explains how to connect HTTP request traces to SQL query execution
so application engineers and DBAs can debug the same incident with shared context.

## Why Use Database Tracing

Without SQL instrumentation, you can see slow API requests but cannot quickly answer:
- which SQL statement caused the latency,
- which request/trace triggered that query,
- whether the issue is app logic, connection pool pressure, or database performance.

With SQLAlchemy instrumentation enabled, each query is attached to the active request trace.
With SQLCommenter enabled, trace metadata is also embedded directly in the SQL text.

## Benefits

| Benefit | What you get |
|---|---|
| Request-to-query correlation | Link slow endpoints to concrete SQL statements in traces |
| Better DBA collaboration | Query logs can carry trace context for cross-team debugging |
| Multi-engine support | Instrument one engine or a list (read/write, shards, tenants) |
| Async and sync coverage | Works for both `Engine` and `AsyncEngine` |
| Controlled metadata | Choose which SQLCommenter fields are injected |

## Prerequisites

Install tracing extras:

```bash
pip install "fastapi-observer[otel,otel-sqlalchemy]"
```

Also install your SQLAlchemy drivers (for example `asyncpg`, `aiomysql`, `psycopg`).

## How It Works

When enabled:
1. FastAPI request creates/joins an OpenTelemetry trace.
2. SQLAlchemy instrumentation creates database spans for executed statements.
3. SQLCommenter appends trace metadata to SQL text (if `enable_commenter=True`).
4. You can correlate API latency, trace spans, and DB-side query logs.

Example SQL comment shape:

```sql
SELECT * FROM users /*db_driver='asyncpg',traceparent='00-<trace-id>-<span-id>-01'*/
```

## Connection pool usage

Engines passed as `db_engine` are also watched for a full connection pool, with or without tracing. A `db.pool_exhausted` warning is written when every connection is checked out, and with metrics on there are pool gauges (not in Prometheus multiprocess mode, where only the warning remains). See [full thread and database pools](logging.md#full-thread-and-database-pools).

## Query count and time on the request line

With tracing on, each request line counts the request's database queries and outbound HTTP calls, from the client spans OpenTelemetry records for them:

```json
{"message": "request.completed", "event": {"path": "/orders", "duration_ms": 182.4, "db": {"count": 12, "ms": 140.3}, "http": {"count": 1, "ms": 31.0}}}
```

`db` counts spans from instrumented database clients, such as the engines passed as `db_engine`. `http` counts spans from instrumented HTTP clients, such as `instrument_httpx_client()` and `instrument_requests_session()`. A request that wasn't sampled has neither field.

### N+1 queries

When one statement runs 10 or more times in a request, usually a query per item in a loop, a warning names it. It's written once per method, route and statement in each process:

```json
{"level": "WARNING", "message": "db.n_plus_one", "request_id": "...", "event": {"method": "GET", "route": "/orders", "statement": "SELECT * FROM items WHERE items.order_id = %(order_id_1)s", "count": 12}}
```

Quoted strings and numbers in the statement are replaced with `?` before it's written. The `request_id` leads to that request's trace.

## Recommended Setup (via `install_observability`)

Use this path when you already initialize observability through the FastAPI facade.

```python
from fastapiobserver import OTelSettings, install_observability
from sqlalchemy import create_engine

engine = create_engine("postgresql://...")

otel_settings = OTelSettings(enabled=True)

install_observability(
    app,
    settings,
    otel_settings=otel_settings,
    db_engine=engine,
)
```

Multiple engines:

```python
install_observability(
    app,
    settings,
    otel_settings=otel_settings,
    db_engine=[write_engine, read_engine],
)
```

Disable SQL comments but keep SQL spans:

```python
install_observability(
    app,
    settings,
    otel_settings=otel_settings,
    db_engine=engine,
    db_commenter_enabled=False,
)
```

Important behavior:
- `db_engine` instrumentation runs only when `otel_settings.enabled=True`.
- Engines are instrumented explicitly from the `db_engine` argument.
- There is no automatic GC-based engine discovery.

## Manual Instrumentation (standalone)

Use manual instrumentation if you do not want to pass engines through `install_observability`.

```python
from fastapiobserver.db_tracing import instrument_sqlalchemy, instrument_sqlalchemy_async

# Sync SQLAlchemy Engine
instrument_sqlalchemy(engine)

# AsyncEngine (internally instruments async_engine.sync_engine)
instrument_sqlalchemy_async(async_engine)
```

Teardown helper:

```python
from fastapiobserver.db_tracing import uninstrument_sqlalchemy

uninstrument_sqlalchemy()
```

## SQLCommenter Options

Default options:

```python
{
    "opentelemetry_values": True,
    "db_driver": True,
    "db_framework": False,
}
```

These are the only keys OpenTelemetry's SQLCommenter supports.

Custom options:

```python
instrument_sqlalchemy(
    engine,
    commenter_options={
        "opentelemetry_values": True,
        "db_driver": False,
        "db_framework": True,
    },
)
```

To instrument several engines, call `instrument_sqlalchemy()` once per engine (or pass a list as `db_engine` to `install_observability()`); every engine stays instrumented. The commenter options of the latest call apply to all of them.

OpenTelemetry's SQLAlchemy instrumentation only supports the SQLAlchemy versions listed by `opentelemetry-instrumentation-sqlalchemy` (currently below 2.1). On other versions nothing is instrumented and fastapi-observer logs `db_tracing.not_instrumented` instead of `db_tracing.instrumented`.

## Verification Checklist

1. Start app with OTel enabled and DB instrumentation configured.
2. Send traffic to endpoints that execute SQL.
3. Confirm database spans appear in your tracing backend.
4. Confirm SQL comments appear in DB logs or SQL echo output when commenter is enabled.
5. Validate that the trace ID in SQL comments matches the corresponding request trace.

Quick request example:

```bash
curl -X POST http://localhost:8000/users/alice
curl http://localhost:8000/users
```

Runnable demo:
- `examples/db_tracing_app.py`

## Edge Cases and Failure Modes

| Scenario | Behavior |
|---|---|
| Missing `otel-sqlalchemy` dependency | Raises clear runtime error with install hint |
| `instrument_sqlalchemy_async` called with non-AsyncEngine | Raises `TypeError` (missing `sync_engine`) |
| `db_engine` set but OTel disabled in `install_observability` | Instrumentation path is skipped |
| `db_commenter_enabled=False` | DB spans still available; SQL comments disabled |
| Request not sampled | No `db` or `http` field on its request line, and no N+1 check |

## Production Best Practices

1. Instrument engines once at process startup, before serving high traffic.
2. Keep SQLCommenter fields minimal to avoid noisy query text.
3. Use parameterized queries everywhere to avoid leaking sensitive literals.
4. Review `db.statement` exports in your tracing backend before production rollout.
5. If SQL comments are not needed for your DBA workflow, disable commenter and rely on spans only.
6. Roll out with canary traffic first and watch trace volume/cost impact.

## Security Note

> [!CAUTION]
> `db.statement` span attributes from SQLAlchemy instrumentation are not scrubbed by
> `fastapi-observer` security redaction. If queries contain inline sensitive values,
> those values can be exported to tracing backends. Use parameterized statements and
> enforce review of trace payloads in production. The `db.n_plus_one` log line
> replaces quoted strings and numbers in the statement with `?`.

---
