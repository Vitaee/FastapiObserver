# Experimental Python operations

The `fastapiobserver.experimental` module observes a sync or async Python
operation using the existing logging, SQLAlchemy and HTTP helpers. It is an
opt-in experiment; its interface and operation event schema can change before
promotion. The stable FastAPI interface and request records are unchanged.

```python
import logging

from fastapiobserver.experimental import observe_operation

with observe_operation("catalog.import"):
    logging.getLogger("catalog").info("catalog.imported")

async def import_catalog():
    async with observe_operation("catalog.import"):
        await fetch_catalog()
```

Configure logging before entering the operation. Without configured handlers,
normal logging levels and routing apply. To use the existing JSON queue:

```python
from fastapiobserver import ObservabilitySettings, setup_logging
from fastapiobserver.logging import flush_logging, shutdown_logging

setup_logging(ObservabilitySettings(service="catalog-import"))
# Run operations here.
flush_logging()
shutdown_logging()  # Only the owner stops shared logging, after all work ends.
```

To retain application-owned logging, keep its configuration and optionally pass
`logger=logging.getLogger("my.application")`. Terminal records capture
correlation before application queues. Existing application log calls still
need their usual correlation filters. Every application destination retains its
own sanitizer, levels, sampling, flushing and shutdown policy.

## Dependency diagnostics

Register SQLAlchemy's existing native hook once for each engine:

```python
from fastapiobserver.db_queries import count_queries

count_queries(engine)  # For an AsyncEngine, use engine.sync_engine.
```

Use `instrument_httpx_client(client)` or `instrument_requests_session()` as
described in [outbound HTTP diagnostics](opentelemetry.md#outbound-http-diagnostics).
Registration and client cleanup remain explicit. The operation installs no
framework middleware, handlers, providers, instrumentors, threads or signals.

Run the [Python-only example](https://github.com/Vitaee/FastapiObserver/blob/main/examples/python_operation.py):

```bash
python -m pip install --pre 'fastapi-observer==2.3.0a2' sqlalchemy httpx
python examples/python_operation.py
```

The example runs two real SQLite queries and one loopback HTTP request, checks
forwarded correlation, flushes JSON logs and closes its resources. It does not
need an external service or tracing provider.

## Operation contract: schema 0.1.0

One entered operation has one terminal outcome: `success`, `failure`, or
`cancelled` for `asyncio.CancelledError`. Exceptions and cancellation propagate
unchanged. Terminal messages are `operation.completed` at INFO,
`operation.failed` at ERROR, and `operation.cancelled` at WARNING.

The `event` contains `schema_version`, `name`, `operation_id`, `outcome`,
`duration_ms`, optional `parent_operation_id`, and observed `db`/`http` totals.
Durations use a monotonic clock; dependency `ms` is summed call time and can
exceed elapsed operation time. No exception message, stack or SQL text is added
by this boundary. Other application logs and third-party spans retain their
existing privacy requirements.

Names must contain 1–128 printable characters and must not contain secrets.
Each context manager is single-use and works with either `with` or `async with`.
Nested operations inherit request correlation and a copy of user-context fields;
their dependency totals are exclusive. Parent totals exclude child calls.
Leaving a child restores the previous state, IDs and user context.

Concurrent tasks may own separate operations. For work on an executor, pass an
explicit `contextvars.copy_context().run` callable; arbitrary new threads do
not inherit operation context automatically. Work outliving an operation should
open a new operation. Late client completion cannot change closed totals, and
closed copied contexts no longer expose the operation's request ID/user context.

## Custom clients and callbacks

For an integration without a maintained adapter, record completed work against
the operation object explicitly:

```python
with observe_operation("catalog.import") as operation:
    operation.record_call("db", 0.005)  # seconds; each invocation is one call
    operation.record_call("http", 0.020, error="read_timeout")
```

For delayed work, capture a monotonic start handle and pass its completion to
the client's callback. It does not rely on the callback's current ContextVar:

```python
with observe_operation("catalog.import") as operation:
    call = operation.start_call("db")
    # Start custom-client work; finish and join its callback before leaving.
    call.finish()
```

`finish()` returns true only for its first admitted completion. Late/closed,
collected or inherited parent-process owners return false. Manual durations
must be finite, nonnegative seconds. Error categories are 1–64 lowercase ASCII
letters/digits/underscores, starting with a letter. At most 32 categories plus
`other` are retained per group; totals remain complete when categories overflow.
Numeric overflow is rejected without modifying existing totals.

The [callback example](https://github.com/Vitaee/FastapiObserver/blob/main/examples/callback_operation.py)
executes real standard-library SQLite work on an executor, records completion
through the handle and joins callbacks before closing. Its logical duration
includes executor queueing. Run `python examples/callback_operation.py`; no
optional client, driver, framework or exporter is needed for that example.

Choose one observing layer for each call. Manual recording cannot automatically
deduplicate arbitrary third-party spans; do not count a call already owned by
an enabled native adapter or sampled fallback. See the
[draft contract](operation-contract.md) for the capability limits.

## Limits of this experiment

- The normal distribution still installs FastAPI and Starlette. Framework-free
  imports and isolated no-deps separation tests are proven; an ordinary
  framework-free installation is not yet a published support claim.
- Counts cover attached/native instrumentation or available sampled-span
  observations. Missing totals are unknown, not measured zero. SQL counts still
  include successful cursor executions only. Separate native SQL failure detail,
  observation coverage and sampling policy are documented in
  [request diagnostics](request-diagnostics.md). No new datastore support is added.
- The boundary borrows active trace context and creates no spans/providers.
  Existing tracing bridges retain native/span deduplication.
- Sampled-span ownership is captured at start, with at most 4,096 tracked client
  spans per provider. Missing/expired/overflowed ownership remains unknown and
  is never reassigned to the completion context. Closing releases tracking slots.
- Library-owned log sampling and failure retention reuse the current pipeline.
  Automatic slow-operation promotion and N+1 warnings are not implemented for
  manual operations. FastAPI retains its existing behavior.
- Queue and held-record count limits remain those of the existing library;
  arbitrary record bytes, concurrency and operation lifetime have no new budget.
  This is not promotion of the full long-lived-operation or production matrix.
- An application's handler may block. Logging exceptions do not replace an
  operation's result or original exception, but failed output can lose records.
  The operation does not promise remote delivery or exactly-once emission.

See the [2.2.1 baseline](baseline.md),
[extraction decision](decisions/0016-experimental-python-operations.md) and
[call ownership decision](decisions/0017-record-calls-against-their-start-owner.md),
with [initial operation evidence](validation/2026-11-04-experimental-core.md) and
[call recording evidence](validation/2026-11-04-call-recording.md).
