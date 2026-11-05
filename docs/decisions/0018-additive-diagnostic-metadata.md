# 0018: Add diagnostic detail without changing established counters

**Status:** accepted for the experiment · **Reviewed:** 2026-11-05

## Context

Request paths do not identify a bounded route, summed call time does not explain
parallelism, and successful SQL counts do not describe failed attempts. Sampled
fallback, explicit hooks and manual reports have different coverage. Logging
sampling also has different ownership from telemetry delivery.

## Decision

- Reuse route resolution at response completion and final diagnostics. Preserve
  the response/background lifetime distinction and existing metric labels.
- Retain legacy DB count/time meanings. Add a separate native SQL execution
  summary, completed by the existing owner-capturing handles and one error hook.
- Serialize dependency summaries through the same state snapshot for requests
  and manual operations. Record fixed source names, unfinished calls and span
  tracking drops; never infer application-wide completeness from a provider.
- Capture sampling policy on terminal records before queues run. Borrowed
  logging stays unknown; cached decisions retain their original probability.
- Provide an engine-scoped budget assertion subscribing to the existing native
  cursor hook. Require instrumentation, include failed attempts/nested scopes,
  and preserve application exceptions. Avoid deriving counts from log output.
- Exercise the configured redaction policy with generated bodies through JSON,
  real in-memory OTLP records and safe failure paths. Keep Hypothesis dev-only.

## Evidence

Route, SQL-execution, observation-metadata and query-budget tests exercise real
FastAPI/Starlette routing, sync/async SQLAlchemy engines, sampled client spans,
copied threads and logging reconfiguration. Redaction property tests generate
encoded/nested/truncated inputs through JSON, real in-memory OTLP and stderr.

## What it cost

The JSON schema additions are optional to old records. Existing 2.2.1 fields
remain frozen, with an explicit projection for the three additive fields and
separate tests for their contracts. SQL failure-only scopes now have an explicit
zero legacy DB count alongside native failure detail. No SQL text is retained.

Additional snapshots and metadata have a measurable serialization/resource cost;
existing CPU/runtime gates remain required. (Since the release after 2.3.0, CPU
comparisons are a [manual check](../benchmarks.md#manual-version-comparison);
the runtime budgets still gate releases.) Sampling metadata is not a delivery
guarantee. A query budget covers one engine's native cursor-entry layer, not all
databases or out-of-context work. No new transport, datastore integration,
critical-path estimator or pytest plugin is introduced.
