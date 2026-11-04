# Common call recording implementation plan

**Goal:** give adapters one completed-call seam and keep completion attached to
its originating operation across threads, nested scopes and delayed callbacks.

**Scope:** the next Python-core slice on `feature/core-call-recording`, from
current `main`, in one follow-up PR. Retain the existing distribution, 27 stable
root exports, FastAPI event schema and successful-query SQL count semantics.
No new framework/datastore adapter or stable portable-contract claim is added.

## Design

- Introduce a small standard-library-only `core/recording.py`. A completed-call
  function validates bounded kinds/categories and finite, nonnegative durations.
  Existing state remains the single aggregation owner; do not duplicate queues,
  telemetry providers, counter storage or fingerprint algorithms.
- A call handle captures its owner and monotonic start, completes at most once,
  and rejects completion after owner closure or process fork. Owner references
  must not keep closed operation/request payloads alive.
- Add experimental `operation.record_call(kind, seconds, error=None)` and
  `operation.start_call(kind)` for custom clients/callbacks. Invalid manual
  metadata is rejected before mutation; expired handles return false.
- Route native SQL and HTTP recording through that seam. SQL captures its owner
  before execution; keep successful cursor executions as the legacy count layer.
- Capture sampled-span ownership in `SpanProcessor.on_start`, with bounded weak
  tracking per provider. Completion in another context must not switch owners;
  unknown/expired/overflowed ownership is ignored, never attributed to the
  current request. Preserve SQLAlchemy and HTTP native/span deduplication.
- Close request-owned state during existing middleware context cleanup, after
  its terminal record/metrics/diagnostics have been constructed.
- Publish a narrow draft operation/dependency contract and fixtures. Broader
  schemas, lifecycle APIs and independent-language conformance remain later work.

## Alternatives

1. Capture ownership at start and share completion recording: selected because
   HTTP, SQL, sampled spans and manual callbacks have real differences in when
   and where they finish, while sharing aggregation and lifecycle rules.
2. Continue selecting owners at completion: simpler locally, but cannot preserve
   nesting or asynchronous callback ownership.
3. Replace request state and split packages now: expands migration risk before
   the completed-call interface is proven. Preserve the compatibility seam.

## Tasks and verification

- [x] Confirm the released baseline suite is green.
- [x] Reproduce span completion credited to a different/nested operation; add
  real SDK tests for origin context, cancellation, GC and bounded tracking.
- [x] Write failing tests for manual recording and exactly-once handles,
  invalid data, copied threads, closure and inherited fork handles.
- [x] Implement the common recorder and route SQL/HTTP/span completion through
  it. Check count/error fields and existing deduplication in both wrapper orders.
- [x] Record the draft contract, capability limits and architecture decision;
  update the roadmap delivery status without marking all M1/M2 complete.
- [ ] Run focused regressions, full tests/coverage, typing/lint/docs, wheel
  separation, real SQL/HTTP/backend checks and paired CPU/production gates.
- [ ] Push one neutral branch and create one follow-up PR. Keep warnings and
  inconclusive performance evidence visible; retain release artifact identity.

## Execution decisions

The user authorized a new branch from updated `main` for this continuation.
The baseline suite passed all 441 tests before implementation. Reproductions
confirmed wrong-owner span completion, lost completion on an unrelated thread,
and native HTTP deduplication incorrectly using the completion context.

Ruling: use one lazy state-owned lifetime token and weak references from native
handles/span tracking. Dropping the token at closure immediately reclaims
tracking slots without scanning a full registry or retaining request payloads.

Ruling: retain the existing aggregation state and fork-reset lock ownership.
This proves the recorder seam before a broader state/package extraction.
