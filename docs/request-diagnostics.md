# Request summaries and query budgets

Release `2.3.0` adds diagnostic fields while retaining existing request
fields, metric names/labels and successful-SQL contributions to `db.count/ms`.
The experimental operation boundary emits the same dependency metadata.

## Route and timing

`event.http.route` is the matched template, such as `/v1/items/{item_id}`.
`event.path` and `url.path` remain the received path. Unknown routes omit
`http.route`; metrics retain their existing `<unmatched>` label. Resolution is
cached for response metrics, the terminal request line and N+1 diagnostics.
Plain Starlette routes and FastAPI router prefixes remain supported.

`duration_ms` still ends at response completion; `app_duration_ms` includes later
background work and cleanup. `db.ms/http.ms` are sums of completed call durations,
not a partition of request latency. Parallel calls can exceed elapsed time.
No critical-path or application-CPU attribution is inferred.

Version `2.3.0` adds three millisecond fields. Times use a monotonic clock and
are rounded to three decimals:

| Field | Meaning | When unknown |
|---|---|---|
| `response_start_ms` | Request entry through successful `send(http.response.start)` completion | No successful response-start send |
| `first_body_ms` | Request entry through the first successful nonempty body send | No nonempty body was successfully sent |
| `stream_duration_ms` | First nonempty body send completion through successful response completion, including final trailers | Empty or unfinished response |

A completed single-chunk body has `stream_duration_ms: 0`. Empty chunks do not
start the stream clock. These measure ASGI sends, including transport
backpressure, **not client-perceived time to first byte**. Headers can be buffered;
a server/proxy can buffer body data or suppress it for HEAD. No extra receive or
send messages are introduced. `response_completed`, `cancelled` and
`client_disconnected` distinguish interrupted responses; unknown times are `null`,
not zero. Background work does not extend a completed stream's timing.

## Diagnostic events on existing traces

When a request already has a recording **server** span, `2.3.0` adds:

| Event | Attributes |
|---|---|
| `fastapiobserver.db.n_plus_one` | `count`, `threshold` (10), `statement_fingerprint` (SHA-256) |
| `fastapiobserver.threadpool.saturated` | `busy`, `limit`, `waiting` |
| `fastapiobserver.db.pool_exhausted` | `checked_out`, `limit` |

Each event is attempted at most once per request (three events maximum), even
when many watched pools are full. The first observed full database pool supplies
the snapshot; its identity/URL is excluded. Events do not create providers,
tracers or child spans, and a faulty application span cannot break the request.
They are independent of process-wide log warning rate limits and remain subject
to the application's sampling, span-event limits and exporter delivery.

N+1 events capture the highest repeated statement count at the final body send,
before ASGI tracing may end the server span. On interruption or later work they
can be added at application completion only if the span is still recording.
Their count is a snapshot; the terminal request log can include later queries.
No raw SQL, parameters or engine URLs are copied into these events. Fingerprints
identify exact statements and are not anonymization. Existing SQL client-span
attributes remain under the instrumentor/provider owner's policy.

Pool pressure is checked at request entry after correlation is established.
It reports a process-wide capacity snapshot, not how long that request waited
or proof that pool pressure caused its latency. Process-wide event-loop lag is
not attached to an arbitrary request span.

See the [validation evidence](validation/2026-11-05-streaming-diagnostics.md)
for compatibility, installed-wheel/backend checks and CPU comparisons.

## SQL execution detail

For an explicitly instrumented SQLAlchemy engine:

```json
{
  "db": {
    "count": 2,
    "ms": 4.2,
    "sqlalchemy": {
      "attempted": 3,
      "succeeded": 2,
      "failed": 1,
      "in_flight": 0,
      "failed_ms": 1.1,
      "errors": {"execution_error": 1}
    }
  }
}
```

The native layer is entry to `before_cursor_execute`, followed by
`after_cursor_execute` or SQLAlchemy's `handle_error` for the same execution
context. Each handle admits one completion against its start owner. An attempt
may remain unfinished at scope closure; late completion does not alter the
terminal record. `attempted = succeeded + failed + in_flight` for admitted native
observations. Failed duration is accumulated separately from legacy `db.ms`.

The bounded categories are `integrity_error`, `disconnect` and `execution_error`.
No SQL, parameters, driver message or connection URL is added. Connection/pool
acquisition, compilation, transaction hooks and result-fetch failures are not
cursor-entry attempts; this is not a complete database-failure monitor.
Retries reaching the hook are separate attempts; executemany is one hook entry.
Other database clients may contribute sampled/manual legacy `db` totals, so
`db.count` is not interchangeable with `db.sqlalchemy.attempted`.

These new fields do not sanitize existing application exception messages or
third-party span payloads; those retain their separate logging/privacy policies.

## Observation coverage

Every terminal record has `observation.coverage`:

| Value | Meaning |
|---|---|
| `unknown` | No dependency observation was recorded; absent groups are not measured zero |
| `instrumented_clients` | Observations came from attached native clients at their documented layers; uninstrumented clients remain unknown |
| `caller_reported` | Only explicit/manual reports were observed; completeness belongs to the caller |
| `partial` | Sampled spans, mixed manual/native sources, unfinished calls or span-tracking overflow prevent a stronger claim |

When available, `sources` maps `db/http` to bounded source names: `sqlalchemy`,
`httpx`, `httpx2`, `requests`, `manual`, `sampled_span`. `in_flight` reports started
handles without an admitted completion. `dropped_spans` reports ownership-tracking
admission failures for this scope. Native counts continue when sampled tracking
is full. The per-provider cap remains 4,096; source names are not arbitrary tags.
Sampled fallback never proves tracing-independent totals or new datastore support.

## Sampling policy and delivery

`log_sampling` is captured before application queues and scope cleanup:

```json
{"owner":"observer","inclusion_probability":0.1,"reason":"sampled"}
```

Reasons are `all`, `sampled`, `warning`, `failure`, `slow`, or `no_identity`.
Warnings and promoted failure/slow records have probability 1.0 under Observer's
policy. Cached sampling decisions retain their original probability if logging
is reconfigured. A missing sampling identity is retained with probability 1.0.

For application-owned logging or an explicitly borrowed operation logger:

```json
{"owner":"application","inclusion_probability":null,"reason":"unknown"}
```

This metadata describes Observer's sampling decision conditional on log-level
admission. It is not an end-to-end delivery probability: filters, bounded queues,
handlers and exporters can still drop records. Other handlers may observe a
record that Observer would discard. Inverse-probability estimates additionally
assume suitable identifier distribution and loss accounting. Use unsampled
request metrics for SLO counts rather than treating weighted logs as ground truth.

## Native query-budget assertion

```python
from sqlalchemy import create_engine, text
from fastapiobserver.db_queries import count_queries
from fastapiobserver.testing import assert_query_budget

engine = create_engine("sqlite://")
count_queries(engine)  # Or install_observability(..., db_engine=engine).
try:
    with assert_query_budget(engine, 1) as budget:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    assert budget.count == 1
finally:
    engine.dispose()
```

This experimental helper requires no pytest plugin, logger, operation boundary,
provider or exporter. It subscribes to the existing native hook for the selected
engine; an AsyncEngine is normalized to its sync engine. Failed attempts and
queries inside nested operations count; manual reports and sampled spans do not.
Missing native hooks at entry or exit produce an error, not a false passing zero.
Existing instrumentation remains owned by its caller.

The guard is single-use and process-local. Normal task context propagation is
supported; executor work needs `copy_context().run`, and must finish before the
guard exits. Retained copied contexts cannot change a closed budget. Exceptions
and cancellation from application code take precedence over a budget assertion.
Queries on other engines and work outside the active context are not covered.
Do not detach/reinstall instrumentation during a guard.

Run the [example](https://github.com/Vitaee/FastapiObserver/blob/main/examples/query_budget.py)
for passing, failed-query and over-budget cases.
