# 0018: Add diagnostic detail without changing established counters

**Status:** accepted, for the experiment · **Reviewed:** 2026-11-05

## Context

A request's `path` is not a bounded route. A sum of call durations hides whether the calls overlapped. `db.count` counts successful queries only, so failed attempts were invisible. Native hooks, manual reports and sampled spans each see a different share of the calls. And deciding to keep a log record is separate from delivering it.

## Decision

- Resolve the route once and reuse it for response metrics, the request line and N+1 diagnostics, as `http.route`. Keep the response and application durations and the existing metric labels.
- Keep `db.count` and `db.ms` as successful executions. Add `db.sqlalchemy`, a separate summary of attempted, succeeded, failed and in-flight queries, completed by the owner-capturing handles from [0017](0017-record-calls-against-their-start-owner.md) and one `handle_error` hook.
- Build dependency summaries from the same state snapshot for requests and operations. `observation` names fixed sources and reports unfinished calls and span-tracking drops. A configured provider is never taken as proof that every call was seen.
- Record the sampling decision as `log_sampling` on terminal records before they reach the queue. With application-owned logging the reason is `unknown`. A cached decision keeps its original probability.
- Add `fastapiobserver.testing.assert_query_budget(engine, max_queries)`, which subscribes to the native cursor hook. It requires attached hooks, counts failed attempts and nested scopes, and lets application exceptions through. It never reads log output.
- Test the configured redaction policy with generated bodies through JSON output, real in-memory OTLP records and the stderr fallback. Hypothesis stays a development dependency.

## Evidence

- [`test_sql_execution_details.py`](https://github.com/Vitaee/FastapiObserver/blob/v2.3.0/tests/test_sql_execution_details.py) runs sync and async engines, including `test_only_failed_sql_keeps_legacy_success_count_at_zero` and `test_connection_failures_are_not_reported_as_cursor_execution_attempts`.
- [`test_observation_metadata.py`](https://github.com/Vitaee/FastapiObserver/blob/v2.3.0/tests/test_observation_metadata.py) covers each coverage value, borrowed loggers, sampled fallback and overflow, and `test_sampling_probability_tracks_the_cached_decision_across_reconfiguration`.
- [`test_query_budget.py`](https://github.com/Vitaee/FastapiObserver/blob/v2.3.0/tests/test_query_budget.py) includes `test_budget_refuses_unattached_native_hooks_instead_of_passing_zero`, nested budgets, threads and a real FastAPI request.
- [`test_redaction_properties.py`](https://github.com/Vitaee/FastapiObserver/blob/v2.3.0/tests/test_redaction_properties.py) generates encoded, nested and truncated inputs through JSON, OTLP and stderr.

## What it cost

- Every new field is optional. The 2.2.1 compatibility probe leaves them out, and each has its own contract test.
- A request whose only queries failed now has an explicit `db.count` of 0 next to `db.sqlalchemy.failed`.
- The extra snapshots and metadata add serialization work to every terminal record.
- `log_sampling` describes selection, not delivery. A query budget covers one engine's cursor-entry layer, not other databases or work outside its context.
