# 0019: Measure ASGI sends and annotate existing server spans

**Status:** accepted · **Reviewed:** 2026-11-05

## Context

`duration_ms` already stops at the final successful body or trailers send, and `app_duration_ms` includes background work. A long stream still could not tell slow headers from a slow first chunk or a slow stream. The N+1 and pool warnings are rate-limited per process, so once the limit was reached a request's trace had no sign of them.

## Decision

- Add `response_start_ms` and `first_body_ms`, measured when the ASGI `send` of `http.response.start` and of the first nonempty body returns. `stream_duration_ms` runs from that first body send to response completion, trailers included. Empty or incomplete responses have no `stream_duration_ms`. These are server-side times, not client receipt. No extra ASGI messages are read and no histograms are added.
- Add three fixed event types to the request's existing recording server span: `fastapiobserver.db.n_plus_one`, `fastapiobserver.threadpool.saturated` and `fastapiobserver.db.pool_exhausted`. Each is added at most once per request, whatever the log rate limit. Attributes are counts, capacities and the existing SHA-256 SQL fingerprint; pool URLs and SQL text are left out. No provider, tracer or child span is created. SDK errors are caught, and SDK calls run outside the aggregation lock.
- Add the N+1 event before the final body is sent, because ASGI tracing can end the server span inside that send. Later queries or an interruption are added only while the borrowed span is still recording.

## Evidence

- The 2.2.1 compatibility probe leaves out exactly the three timing fields.
- [`test_response_timing.py`](https://github.com/Vitaee/FastapiObserver/blob/v2.3.0/tests/test_response_timing.py): `test_stream_timing_uses_successful_nonempty_sends`, `test_empty_response_has_no_body_or_stream_timing` and `test_failure_before_headers_leaves_timings_unknown`.
- [`test_diagnostic_spans.py`](https://github.com/Vitaee/FastapiObserver/blob/v2.3.0/tests/test_diagnostic_spans.py): `test_only_three_fixed_event_types_are_admitted_under_concurrent_calls`, `test_n_plus_one_reaches_each_server_span_before_transport_ends_it` and `test_pool_events_are_bounded_private_and_independent_of_log_rate_limit`.
- Real-server shutdown and streaming cases run in [`test_production_runtime.py`](https://github.com/Vitaee/FastapiObserver/blob/v2.3.0/tests/test_production_runtime.py). Release validation compared CPU per request with 2.2.1 and the previous alpha using the paired harness.

## What it cost

- Three timing fields and a three-entry set, created lazily, add a little work per request.
- The span event's count can be lower than the final log line's totals, since the event is added before the last body is sent.
- Pool events are capacity snapshots, not measured wait time or the cause of latency.
