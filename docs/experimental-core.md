# Experimental Python operations

`fastapiobserver.experimental.observe_operation()` observes a sync or async
operation outside a request, such as a job or a CLI command, with the same
logging, SQLAlchemy and HTTP helpers. Its interface and event schema can change
in any release; the FastAPI interface and request fields keep their usual
compatibility.

```python
import logging

from fastapiobserver.experimental import observe_operation

with observe_operation("catalog.import"):
    logging.getLogger("catalog").info("catalog.imported")

async def import_catalog():
    async with observe_operation("catalog.import"):
        await fetch_catalog()
```

Configure logging before entering the operation. To use the library's JSON
queue:

```python
from fastapiobserver import ObservabilitySettings, setup_logging
from fastapiobserver.logging import flush_logging, shutdown_logging

setup_logging(ObservabilitySettings(service="catalog-import"))
# Run operations here.
flush_logging()
shutdown_logging()  # Only the owner stops shared logging, after all work ends.
```

To keep application-owned logging, pass
`logger=logging.getLogger("my.application")`. The terminal record gets its
correlation fields before your handlers run. Your own log calls still need
their usual correlation filters.

Each operation ends with one `operation.completed`, `operation.failed` or
`operation.cancelled` record. Its fields, nesting and ownership rules are in
the [draft contract](https://github.com/Vitaee/FastapiObserver/tree/main/spec).

## Dependency diagnostics

Register SQLAlchemy's native hook once for each engine:

```python
from fastapiobserver.db_queries import count_queries

count_queries(engine)  # For an AsyncEngine, use engine.sync_engine.
```

For HTTP clients, use `instrument_httpx_client(client)` or
`instrument_requests_session()` as described in
[outbound HTTP diagnostics](opentelemetry.md#outbound-http-diagnostics).

Run the [Python-only example](https://github.com/Vitaee/FastapiObserver/blob/main/examples/python_operation.py),
which makes two SQLite queries and one loopback HTTP request:

```bash
python -m pip install fastapi-observer sqlalchemy httpx
python examples/python_operation.py
```

## Custom clients and callbacks

For a client with no helper, record completed work on the operation object:

```python
with observe_operation("catalog.import") as operation:
    operation.record_call("db", 0.005)  # seconds; each invocation is one call
    operation.record_call("http", 0.020, error="read_timeout")
```

For work that finishes in a callback, take a start handle and finish it from
the callback:

```python
with observe_operation("catalog.import") as operation:
    call = operation.start_call("db")
    # Start custom-client work; finish and join its callback before leaving.
    call.finish()
```

`finish()` returns `True` only for the first completion. The
[callback example](https://github.com/Vitaee/FastapiObserver/blob/main/examples/callback_operation.py)
runs SQLite work on an executor and records it through a handle:
`python examples/callback_operation.py`.

Do not record a call by hand that a helper or a sampled span already counts.

## Limits

- FastAPI and Starlette are still installed with the package, though the
  operation API imports without them.
- Slow operations do not write their held records, and N+1 warnings are not
  logged for operations.
- Held records and the log queue are limited by count, not by size.
- A blocking application handler blocks the operation. A logging error never
  replaces the operation's result or exception, but its record is lost.

See decisions [0016](decisions/0016-experimental-python-operations.md) and
[0017](decisions/0017-record-calls-against-their-start-owner.md), and the
[2.2.1 baseline](baseline.md).
