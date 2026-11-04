# 0017: Record calls against their start owner

**Status:** accepted for the experiment · **Reviewed:** 2026-11-04

## Context

Native HTTP already retained request state, while SQL and sampled-span fallback
selected current state at completion. A span ending in a nested operation could
be credited to the wrong owner; an unrelated completion thread lost its count.
Current-context HTTP deduplication could also drop or double-count a late span.

## Decision

- Use one completed-call admission seam and exactly-once monotonic handles.
  Preserve the existing aggregation fields, SQL successful-execution layer,
  fingerprint algorithm, logging/provider ownership and stable root interface.
- Let state own one lazy lifetime token, borrowed weakly by handles and bounded
  sampled-span tracking. Close invalidates it after final diagnostics are built;
  neither callback handles nor trackers retain closed request payloads.
- Capture SQL/span/native-HTTP ownership at start, never from the completion
  context. Keep native/span deduplication attached to that original owner.
- Expose explicit experimental manual recording for custom clients. Bound error
  categories and reject invalid/nonfinite input before it can corrupt totals.
- Keep a narrow draft JSON contract with positive/negative fixtures. Do not
  promote the full portable specification or claim new automatic adapters.

## Evidence

`test_call_ownership.py` reproduces the original failures using real SDK spans,
nested operations, independent threads and both HTTP deduplication contexts.
`test_call_recording.py` checks once-only concurrent completion, expiry, weak
ownership, immediate capacity reclamation, fork inheritance, numeric/category
bounds and SQL ownership. `test_operation_contract.py` validates fixtures and
real emitted events. The tagged FastAPI compatibility fixture remains required.

## What it cost

Active calls pay for a small handle/token and short ownership/aggregation locks.
Sampled tracking is capped at 4,096 entries per provider; overflow remains
partial/unknown and does not affect native counts. Manual adapters still select
their observing layer and must avoid duplicate third-party instrumentation.
Generic state extraction, lifecycle/provider APIs, byte budgets and independent
language conformance remain future slices. The JSON Schema validator is a dev
dependency; it is not added to normal application installations.
