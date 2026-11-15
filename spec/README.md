# Draft operation and completed-call contract

Status: experimental draft, event schema **0.1.0**. The schema version changes
independently of the Python package. Usage is in the
[experimental operations guide](https://vitaee.github.io/FastapiObserver/experimental-core/).

- [`operation-event.schema.json`](operation-event.schema.json) describes the
  `event` of an operation's terminal log record.
- [`fixtures/operation-events.json`](fixtures/operation-events.json) holds valid
  and invalid examples.
- `tests/test_operation_contract.py` validates the fixtures and real emitted
  events. Ownership, concurrency, expiry and bounds are covered by
  `tests/test_call_ownership.py` and `tests/test_call_recording.py`, since a
  schema check cannot test them.

## Event

| Outcome | Message | Level |
|---|---|---|
| `success` | `operation.completed` | `INFO` |
| `failure` | `operation.failed` | `ERROR` |
| `cancelled` (`asyncio.CancelledError`) | `operation.cancelled` | `WARNING` |

Required fields are `schema_version`, `name`, `operation_id`, `outcome` and
`duration_ms`. Optional fields are `parent_operation_id`, `db`, `http`,
`observation` and `log_sampling`. Identifiers are 32 lowercase hexadecimal
digits. Output is UTF-8 JSON with finite numbers. Consumers ignore unknown
fields.

A name has 1–128 printable characters (`str.isprintable()`). `duration_ms` is
monotonic elapsed time. A dependency group's `ms` is the sum of its completed
calls, so it can exceed `duration_ms` when calls overlap. The event carries no
exception message, stack trace or SQL text. A missing `db` or `http` group means
nothing was observed; read it as unknown, not zero.

## Lifecycle and context

- An operation is entered once, with `with` or `async with`. Exceptions and
  cancellation propagate unchanged. The cause of a cancellation is not recorded.
- Entering an operation installs no logging handler, provider, exporter,
  watcher, signal handler or driver hook.
- A nested operation inherits the request ID and a copy of the user context.
  Its call totals are its own; the parent's totals exclude them. Leaving it
  restores the outer state.
- Concurrent tasks can each own an operation. Work on an executor needs an
  explicit `contextvars.copy_context().run`; new threads start without the
  operation's context.
- Closing an operation invalidates its recording token. Closed copied contexts
  stop exposing its request ID and user context.

## Completed-call ownership

- A call belongs to the operation where it started. A start handle captures
  that operation, a monotonic start time and the process ID.
- `finish()` is admitted once, even with concurrent callbacks. It returns
  `False` for a closed, collected or parent-process owner, and the call is not
  given to whichever operation is current.
- `record_call()` records against the operation object it is called on,
  whatever thread, task or context calls it.
- Handles and sampled-span tracking hold the state's lifetime token weakly, so
  closing frees their entries even while the application keeps the operation
  object.
- Sampled client spans capture their owner at span start, keyed by trace and
  span ID. Each tracer provider tracks at most 4,096 active spans; spans beyond
  that are left out of the totals.
- Native HTTP deduplication uses the owner captured at start, in either
  instrumentor wrapping order. SQLAlchemy spans are skipped because the engine
  hooks already count those queries.

## Input and bounds

Call kinds are `db` and `http`. Recorded durations are finite, nonnegative
seconds; the accumulated milliseconds must stay finite. `record_call()` raises
`ValueError` or `TypeError` on invalid input before changing anything. Invalid adapter input,
an expired owner or numeric overflow returns `False` and leaves totals as they
were.

An error category matches `[a-z][a-z0-9_]{0,63}`. Each group keeps 32 named
categories; later ones count under `other`, so total and error counts stay
complete. Categories are codes, never exception messages, user IDs or URLs.

SQL statements are kept only as SHA-256 fingerprints, at most 128 per request.
A fingerprint can still be matched by guessing the statement, so it is not
anonymisation.

## Count layers

| Source | What one call is | Notes |
|---|---|---|
| SQLAlchemy engine hooks | A successful cursor execution | Failures go to `db.sqlalchemy` only; trace sampling has no effect |
| `instrument_httpx_client()`, `instrument_requests_session()` | One `send()`, until the response or stream is consumed or closed | A call with a client span counts once |
| `record_call()`, `start_call()` | Whatever the adapter author chose | Each `record_call()` is one call; a handle finishes once |
| Sampled client spans | A finished recording `CLIENT` span | Partial: depends on sampling and the 4,096-span cap |

Record each call through one source. Recording a call by hand that a native
hook or a sampled span already counts counts it twice.

`db.sqlalchemy`, `observation` and `log_sampling` are described in
[request diagnostics](https://vitaee.github.io/FastapiObserver/request-diagnostics/).
`log_sampling` records the selection made before the queue. Queue overflow,
sink failures and export losses are counted separately.

## Open before 1.0

Configuration precedence, metric and semantic-convention mappings, integers
beyond JavaScript's safe range, Unicode rules for names, byte and depth limits,
adapter capability manifests, shutdown deadlines, other cancellation models and
a second implementation in another language.
