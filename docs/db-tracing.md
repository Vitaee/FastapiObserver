# Database Tracing and SQL Diagnostics (SQLAlchemy)

## Query count and time on the request line

Every HTTP and WebSocket log record counts database queries and instrumented
outbound HTTP calls without requiring OpenTelemetry:

```json
{"message": "request.completed", "event": {"path": "/orders", "duration_ms": 182.4, "db": {"count": 12, "ms": 140.3, "sqlalchemy": {"attempted": 12, "succeeded": 12, "failed": 0, "in_flight": 0, "conn_held_ms": 142.1}}, "http": {"count": 1, "ms": 31.0}}}
```

- `db` counts queries on engines passed as `db_engine` (both `Engine` and `AsyncEngine`). Other database clients (such as Redis) are counted from sampled OpenTelemetry spans when tracing is enabled.
- `http` counts clients registered with `instrument_httpx_client()` or `instrument_requests_session()` independently of tracing; see [outbound HTTP diagnostics](opentelemetry.md#outbound-http-diagnostics).
- Engines passed as `db_engine` are also watched for pool saturation (`db.pool_exhausted`); see [full thread and database pools](logging.md#full-thread-and-database-pools).

### SQL execution detail

`db.count` and `db.ms` count successful executions. `db.sqlalchemy` also counts
failures:

```json
{"db": {"count": 2, "ms": 4.2, "sqlalchemy": {"attempted": 3, "succeeded": 2, "failed": 1, "in_flight": 0, "failed_ms": 1.1, "conn_held_ms": 48.5, "errors": {"execution_error": 1}}}}
```

- `attempted = succeeded + failed + in_flight`, from `before_cursor_execute`, `after_cursor_execute` and `handle_error`.
- `conn_held_ms` is the total time connections were checked out of the engine's pool during the request (`checkout` to `checkin`).
- `errors` uses three categories: `integrity_error`, `disconnect` and `execution_error`. No SQL text, parameters or connection URLs are included.

### N+1 queries (`db.n_plus_one`)

When a statement executes 10 or more times in a single request, `fastapi-observer`
adds `"db.n_plus_one"` to `event.diagnostics`, increments
`fastapiobserver_db_n_plus_one_total{route}`, and logs a rate-limited warning
once per `(method, route, statement_fingerprint)`:

```json
{"level": "WARNING", "message": "db.n_plus_one", "request_id": "...", "event": {"method": "GET", "route": "/orders", "statement_fingerprint": "<sha256>", "count": 12, "relationship": "Order.items", "code.file.path": "/app/orders.py", "code.line.number": 41, "code.function.name": "orders.list_orders"}}
```

- `statement_fingerprint` is a SHA-256 digest of the statement without logging raw SQL text or parameters (up to 128 distinct fingerprints per request and 1,024 warning keys per middleware instance).
- `code.*` identifies the innermost application frame that triggered the 10th execution.
- `relationship` names the SQLAlchemy ORM relationship (e.g. `Order.items`) when triggered by lazy or relationship loading, even if serialized after the route handler returned.

### Slow queries (`db.slow_query`)

When one statement takes 1 second or longer, the request line's `event.diagnostics`
gets `"db.slow_query"`, `fastapiobserver_db_slow_query_total{route}` counts the
request, and a warning names the request's slowest statement, once per
`(method, route, statement_fingerprint)`:

```json
{"level": "WARNING", "message": "db.slow_query", "request_id": "...", "event": {"method": "GET", "route": "/reports", "statement_fingerprint": "<sha256>", "duration_ms": 2140, "code.file.path": "/app/reports.py", "code.line.number": 27, "code.function.name": "reports.monthly"}}
```

Fields match the N+1 warning: no SQL text, the application line that ran the
statement, and `relationship` for an ORM relationship load. Failed statements are
not included; their time is in `db.sqlalchemy.failed_ms`.

### Connections held across outbound HTTP calls (`db.connection_held_during_http`)

Holding a SQLAlchemy session/connection open while awaiting an external HTTP API
pins a scarce pool connection for the duration of the network call. When an
instrumented outbound HTTP call overlaps an open DB connection **and** total
connection checkout time (`db.sqlalchemy.conn_held_ms`) is at least `10 × db.ms`
(or the connection remains checked out at request end), `fastapi-observer`:
1. Appends `"db.connection_held_during_http"` to the request's `event.diagnostics` list.
2. Increments `fastapiobserver_db_connection_held_during_http_total{route}`.
3. Emits a rate-limited `db.connection_held_during_http` warning once per `(method, route, target)` with the HTTP `target` (method and host) and the application line that made the call.

### Connections held idle (`db.connection_held_idle`)

A session kept open through other work, such as a CPU-heavy step, a sleep or an
LLM call through a client the library doesn't instrument, holds its connection
out of the pool while the database does nothing. When a request held connections
at least `10 × db.ms` and at least 100 ms longer than its queries ran, and no
outbound HTTP call explains it, the request line's `event.diagnostics` gets
`"db.connection_held_idle"`, `fastapiobserver_db_connection_held_idle_total{route}`
counts the request, and a warning is written once per `(method, route)`:

```json
{"level": "WARNING", "message": "db.connection_held_idle", "event": {"method": "GET", "route": "/reports", "conn_held_ms": 2340, "db_ms": 12}}
```

Read what you need, close the session, then do the slow work; open a new session
if you need to write afterwards.

## OpenTelemetry SQL Spans and SQLCommenter

To emit per-query OpenTelemetry spans and inject `traceparent` comments into SQL text:

```bash
pip install "fastapi-observer[otel,otel-sqlalchemy]"
```

```python
from fastapiobserver import OTelSettings, install_observability
from sqlalchemy import create_engine

engine = create_engine("postgresql://...")

install_observability(
    app,
    settings,
    otel_settings=OTelSettings(enabled=True),
    db_engine=engine,               # Single engine or list: [write_engine, read_engine]
    db_commenter_enabled=True,      # Set False to keep SQL spans without SQL comments
)
```

When `db_commenter_enabled=True`, executed statements include trace correlation comments:

```sql
SELECT * FROM users /*db_driver='asyncpg',traceparent='00-<trace-id>-<span-id>-01'*/
```

For standalone instrumentation outside `install_observability()`:

```python
from fastapiobserver.db_tracing import (
    instrument_sqlalchemy,
    instrument_sqlalchemy_async,
    uninstrument_sqlalchemy,
)

instrument_sqlalchemy(
    engine,
    commenter_options={
        "opentelemetry_values": True,
        "db_driver": True,
        "db_framework": False,
    },
)
```

## Edge Cases and Security

| Scenario | Behavior |
|---|---|
| `db_engine` passed with OTel disabled | Native query counting, `conn_held_ms`, pool checks, and N+1 detection run; OTel span instrumentation is skipped |
| Request not sampled by OTel or `OBS_LOG_SAMPLE_RATE` | Native SQLAlchemy metrics and anomaly counters still run on every request |
| SQLAlchemy >= 2.1 | OTel's `opentelemetry-instrumentation-sqlalchemy` logs `db_tracing.not_instrumented`; native query counts and N+1 still work |

> [!CAUTION]
> OpenTelemetry's SQLAlchemy span attributes (`db.statement`) are not scrubbed by
> `fastapi-observer` log redaction. Always use bound SQL parameters rather than inline
> literals. Native `db.n_plus_one` logs only emit a SHA-256 `statement_fingerprint`.
