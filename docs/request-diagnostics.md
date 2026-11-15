# Request summaries and query budgets

## Route and timing

`event.http.route` is the matched template, such as `/v1/items/{item_id}`.
`event.path` and `url.path` remain the received path. Unknown routes omit
`http.route`; metrics use `<unmatched>`. At lifespan startup, `fastapi-observer`
also inspects the registered routing table once and logs `route.duplicate` or
`route.shadowed` warnings when an earlier static or less-specific route masks a
later route (for example `/users/{id}` registered before `/users/me`).

For `duration_ms`, `app_duration_ms`, `db.ms` and `http.ms`, see
[what the durations mean](logging.md#what-the-durations-mean). The streaming and
payload fields use a monotonic clock rounded to three decimals:

| Field | Meaning | When unknown |
|---|---|---|
| `response_start_ms` | Request entry through `send(http.response.start)` completion | `null` if no response-start send succeeded |
| `first_body_ms` | Request entry through the first nonempty body chunk send | `null` if no nonempty body was sent |
| `stream_duration_ms` | First nonempty body send through final body/trailer completion (`0.0` for single-chunk responses) | `null` if empty or interrupted |
| `response_bytes` | Total bytes sent across `http.response.body` chunks | `0` for empty responses |

For `after_disconnect_ms` and `after_disconnect_calls`, see
[client disconnects](servers.md#client-disconnects-and-shutdown).

## Per-request `diagnostics` and 422 validation errors

When one or more anomalies occur during an HTTP request or WebSocket session,
`event.diagnostics` lists the triggered machine-readable tags on that request's
log record:

| Tag in `event.diagnostics` | Trigger |
|---|---|
| `db.n_plus_one` | The same SQL statement text (compared by SHA-256, never logged) executed `>= 10` times in the request |
| `http.n_plus_one` | Outbound HTTP calls to the same `(method, host)` ran `>= 10` times in the request |
| `db.connection_held_during_http` | A SQLAlchemy connection stayed checked out across an outbound HTTP call and was held `>= 10 × db.ms` (or remained checked out at request end) |
| `db.pool_exhausted` | Request started while a watched SQLAlchemy `QueuePool` was at full capacity |
| `threadpool.saturated` | Request started while AnyIO's worker thread pool had `0` free tokens and calls were waiting for one |
| `event_loop.blocked` | The event-loop watchdog detected a stall while this request was in flight |

On `422 Unprocessable Entity` responses, `event.validation_errors` captures up to
16 failed field locations and error types (`[{"loc": "body.items.0.price", "type": "greater_than"}]`,
never raw input values), and increments `fastapiobserver_http_validation_errors_total{route, field}`
with list indices normalized to `[]` (`body.items.[].price`).

When a request also has a recording OpenTelemetry **server** span, `fastapiobserver.db.n_plus_one`,
`fastapiobserver.threadpool.saturated`, and `fastapiobserver.db.pool_exhausted` are attached as span
events (at most once each per request) without raw SQL or engine URLs.

## SQL execution detail

The `db.sqlalchemy` breakdown of attempted, failed and in-flight queries is
described in [database tracing](db-tracing.md#sql-execution-detail).

## Observation coverage

Every terminal record includes `observation.coverage`:

| Value | Meaning |
|---|---|
| `unknown` | No dependency observation was recorded |
| `instrumented_clients` | Observations came from attached native hooks (`sqlalchemy`, `httpx`, `httpx2`, `requests`) |
| `caller_reported` | Only explicit/manual reports were observed |
| `partial` | Sampled spans, mixed manual/native sources, unfinished calls (`in_flight`), or span-tracking overflow (`dropped_spans`) |

## Sampling policy and delivery

`log_sampling` records Observer's retention decision before queue delivery:

```json
{"owner": "observer", "inclusion_probability": 0.1, "reason": "sampled"}
```

Reasons are `all`, `sampled`, `warning`, `failure`, `slow`, or `no_identity`
(or `{"owner": "application", "inclusion_probability": null, "reason": "unknown"}`
when `configure_logging=False`). Use unsampled Prometheus request metrics for
SLO traffic counts rather than inverse-weighting sampled logs.

## Native query-budget assertion

```python
from sqlalchemy import create_engine, text
from fastapiobserver.db_queries import count_queries
from fastapiobserver.testing import assert_query_budget

engine = create_engine("sqlite://")
count_queries(engine)  # Or install_observability(..., db_engine=engine).
with assert_query_budget(engine, 1) as budget:
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
assert budget.count == 1
```

`assert_query_budget` requires no pytest plugin or OTel provider and counts all
admitted `before_cursor_execute` attempts on the target `Engine` or `AsyncEngine`
within the active `contextvars` scope. See
[`examples/query_budget.py`](https://github.com/Vitaee/FastapiObserver/blob/main/examples/query_budget.py).
