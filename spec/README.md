# Draft operation and completed-call contract

Status: **experimental, incomplete**. The JSON representation remains event
schema **0.1.0**. Python API and library versions evolve separately. This draft
records the implemented operation/call slice; it is not contract 1.0, a new
transport protocol, or certification of another-language implementation.

## Representation and lifecycle

`operation-event.schema.json` describes the `event` inside an operation terminal
log record. The surrounding existing JSON log envelope has its own schema.
Output is UTF-8 JSON with finite numbers. Unknown fields are allowed so consumers
can ignore additions. Required identifiers are lowercase 32-digit hexadecimal
strings. Missing dependency groups mean no observations or unknown coverage;
they must not be interpreted as a measured zero.

An operation has a single start and terminal outcome: `success`, `failure`, or
`cancelled` for observed runtime cancellation. Python currently recognizes
`asyncio.CancelledError`. A cause such as shutdown/disconnect is not inferred.
The operation is single-use; exceptions/cancellation propagate unchanged.
Closing restores outer context and invalidates the operation's recording token.
No logging handler, provider, exporter, watcher, signal handler or driver hook is
installed by entering an operation.

`duration_ms` is monotonic operation elapsed time. Dependency `ms` is accumulated
completed-call time and may exceed elapsed time when work overlaps. Child
operation totals are exclusive; the parent does not aggregate them again.
This timing does not replace FastAPI's existing response/application distinction.

## Completed-call ownership

- A start handle captures the originating operation, monotonic start and process.
  Its completion is admitted at most once, including concurrent callbacks.
- A manual completed-call API records against the explicit operation object,
  independently of the callback's current thread/task/context.
- Handles and captured spans reject closed, collected or parent-process owners.
  A missing owner never falls back to whichever operation is currently running.
- State owns one lazy lifetime token. Calls and sampled-span tracking borrow it
  weakly. Closing releases tracking entries even when the application retains
  the operation object; late handles cannot keep request payloads alive.
- Sampled spans capture ownership at span start, keyed by trace/span identity.
  The per-provider registry admits at most 4,096 active entries. Overflowed
  observations are omitted; they remain unknown rather than being reassigned.
- Native HTTP deduplication captures ownership at start, including either
  instrumentor wrapping order. Another active HTTP client at completion cannot
  suppress an unrelated span. SQLAlchemy's span scope remains excluded because
  its native engine hooks own those counts.

## Input, bounds and privacy

Supported call kinds are `db` and `http`. Durations are seconds at the recording
interface, finite and nonnegative; emitted accumulated milliseconds must remain
finite. Invalid manual metadata raises before mutation. Invalid adapter input,
expired ownership and aggregate overflow return false without changing totals.
A rejected `finish()` attempt is not retried on another owner.

Error categories contain 1–64 ASCII lowercase letters/digits/underscores and
start with a letter. Each group keeps at most 32 named categories plus `other`;
overflow categories join `other`, preserving total/error counts. Category values
are codes, never exception messages, user identifiers or URLs.

The recorder does not retain raw statements: the existing full-statement SHA-256
algorithm and cap of 128 fingerprints remain unchanged. A fingerprint is not
anonymization. The operation boundary emits no SQL text or exception details.
Application context/logging and upstream span payloads retain their separate
sanitizer and policy requirements.

## Count layer and capability limits

| Source | Implemented observation layer | Completeness / deduplication |
|---|---|---|
| SQLAlchemy engine hooks | Successful completed cursor execution | Failed executions are excluded; trace sampling cannot change native totals |
| Explicit HTTP helpers | Client send through stream consumption/close | Status/transport/cancellation categories; native/client span counts once |
| Custom manual adapter | Layer selected by adapter author | Each `record_call` is a new observation; one handle finishes once |
| Sampled client spans | Completed recording CLIENT span | Partial by sampling, hook availability and ownership-tracking capacity |

Do not manually record a call already owned by a native adapter or sampled-span
fallback. Automatic deduplication of arbitrary custom-client spans is not
implemented. A new adapter must declare its observing layer and ownership before
claiming tracing-independent or complete counts. The manual API alone does not
establish tested Redis/MongoDB/Tortoise support.

The fixture/schema tests validate structure, finite JSON serialization and real
emitted events. Behavioral tests separately cover ownership, concurrency,
deduplication, expiry, resource bounds and exception preservation. Schema
validation alone is not runtime conformance.

## Decisions still blocking portable 1.0

Common configuration precedence, full metric/semantic-convention mappings,
integer precision beyond JavaScript's safe range, Unicode name validation,
byte/depth budgets, adapter capability manifests, deadlines/delivery policy,
broader cancellation models and independent-language validation remain open.
Python's current operation names use `str.isprintable()` and a 128-character cap;
the schema captures structural limits rather than defining Unicode categories.
