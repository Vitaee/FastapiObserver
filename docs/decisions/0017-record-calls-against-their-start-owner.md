# 0017: Record calls against their start owner

**Status:** accepted, for the experiment · **Reviewed:** 2026-11-04

## Context

Native HTTP calls already stayed with the request that started them. SQL queries and sampled client spans went to whichever state was current when they finished. A span that ended inside a nested operation was credited to the wrong owner, and one that finished on an unrelated thread was lost. HTTP deduplication also looked at the current context, so a late span could be dropped or counted twice.

## Decision

- Every completed call goes through `record_completed_call()` or a `CallHandle` that finishes once on a monotonic clock ([`core/recording.py`](https://github.com/Vitaee/FastapiObserver/blob/v2.3.0/src/fastapiobserver/core/recording.py)). The aggregation fields, SQL counts of successful executions, the fingerprint algorithm, logging and provider ownership and the root API stay the same.
- Each state owns one lifetime token, created lazily. Handles and the sampled-span tracker hold it weakly. Closing invalidates it after the final diagnostics are built, so neither keeps a closed request's payload alive.
- SQL queries, sampled spans and native HTTP calls capture their owner when they start. Native/span deduplication stays with that owner.
- Operations get `record_call()` and `start_call()` for custom clients. Error categories are bounded, and invalid or non-finite input is rejected before it reaches the totals.
- `spec/` holds a narrow draft JSON contract with valid and invalid fixtures. The full portable specification and new automatic adapters are separate decisions.

## Evidence

- [`test_call_ownership.py`](https://github.com/Vitaee/FastapiObserver/blob/v2.3.0/tests/test_call_ownership.py) reproduces the original failures with real SDK spans, nested operations, independent threads and both HTTP deduplication contexts, for example `test_late_child_span_cannot_be_credited_to_parent`.
- [`test_call_recording.py`](https://github.com/Vitaee/FastapiObserver/blob/v2.3.0/tests/test_call_recording.py) checks once-only completion across threads, expiry, weak ownership, immediate release of tracking capacity, handles inherited across `fork()`, numeric and category bounds, and SQL ownership.
- [`test_operation_contract.py`](https://github.com/Vitaee/FastapiObserver/blob/v2.3.0/tests/test_operation_contract.py) validates the fixtures and real emitted events against the schema. The `v2.2.1` compatibility fixture still passes.

## What it cost

- Each active call pays for a small handle and token, and takes short ownership and aggregation locks.
- Sampled-span tracking is capped at 4,096 entries per provider. Spans beyond that are left out of the totals and the record's coverage becomes `partial`. Native counts are unaffected.
- A manual adapter chooses its own observation layer, and must skip calls that other instrumentation already counts.
- Generic state extraction, lifecycle and provider APIs, byte limits and conformance in other languages are left for later.
- `jsonschema` is a development dependency only.
