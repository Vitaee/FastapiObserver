# 0019 — Measure ASGI sends and annotate existing server spans

**Status:** Accepted

**Date:** 5 November 2026

## Context

Request duration already stops at the final successful body/trailers send while
application duration includes background work. A long stream still needs to
distinguish delayed headers, delayed first content and time spent streaming.
Existing N+1 and pool warnings also lack a per-request trace breadcrumb when
their process-wide log rate limit has already been reached.

## Decision

Add response-start and first nonempty body elapsed times, measured after the
corresponding ASGI send returns. Stream duration runs from that first body send
through response completion, including trailers. Empty or incomplete responses
have no completed stream duration. These are server observations, not proof of
client receipt; no extra ASGI messages or timing histograms are needed.

Reuse the request's captured recording server span for three fixed diagnostic
event types. Admit each type once per request, independently of log rate limits.
Use only numeric capacity/count fields and the existing SHA-256 SQL fingerprint;
exclude pool URLs and SQL text. Never create providers, tracers or child spans.
SDK errors are contained and SDK calls run outside the aggregation lock.

Emit the N+1 snapshot before sending the final body, since ASGI tracing can end
the server span inside that send. Finalization may record an interruption or
later queries only while the borrowed span is still recording. Consequently the
trace event's count can precede the final log's totals. Pool events describe a
capacity snapshot, not request wait time or causal latency attribution.

## Evidence

The frozen 2.2.1 comparison excludes exactly the new timing fields. Deterministic
ASGI timing tests, real-server shutdown/stream tests and native SQL/SDK tests check
the additions. The existing paired CPU harness compares both stable and prior
alpha sources; runtime and backend gates remain required.

## What it cost

Three timing fields and a lazy, three-entry deduplication set add bounded work.
Existing request/count/metric meanings, warning limits, sampling and provider
ownership remain unchanged. Redaction and application sanitizer paths stay in
place; redundant mapping copies/runtime typing checks are removed.
