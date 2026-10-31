# 0009: Sample logs per request, by trace ID

**Status:** accepted · **Reviewed:** 2026-10-31

## Context

Successful requests write most of the log volume, and `fastapiobserver_log_bytes_total` shows which routes. Dropping a share of lines at random would leave requests with half their lines, and lose the lines a failure needs. Traces are already sampled by trace ID, so logs sampled any other way would point at traces that were never kept.

## Decision

- `LOG_SAMPLE_RATE` decides per request, not per line. A request is sampled when the low 64 bits of its trace ID are below the ratio's bound. That's the rule OpenTelemetry's `TraceIdRatioBased` sampler uses. Without a trace ID, a 64-bit hash of the request ID is used instead. Services forward both, so every service that sees a request makes the same decision.
- Only `INFO` and lower lines are sampled. For a request that isn't sampled, they're held in the per-request state that `LOG_LEVEL_ON_ERROR` already uses ([0008](0008-one-state-object-per-request.md)). They're written if the request fails or takes `LOG_SLOW_REQUEST_MS` or longer. `WARNING` and higher are always written.
- Metrics aren't sampled.

## Evidence

- `test_sampling_follows_the_trace_id_as_opentelemetry_does` sends two traces at a ratio of 0.5. The one whose lines are written is the one OpenTelemetry's sampler keeps.
- `test_unsampled_requests_write_only_warnings_unless_they_fail_or_are_slow` checks the failed, slow and `WARNING` rules.
- CPU per request, log line included, from [`examples/benchmarks/request_cpu.py`](https://github.com/Vitaee/FastapiObserver/blob/main/examples/benchmarks/request_cpu.py) on an Apple M1, median of 6 runs:

| | CPU per request |
|---|---:|
| 1.10.0 | 51.1 µs |
| Sampling off | 51.2 µs |
| `LOG_SAMPLE_RATE=0.1` | 38.0 µs |

## What it cost

- **Logs and traces line up only at matching ratios.** If each service samples traces with OpenTelemetry's ratio sampler at the same ratio, a request's lines are kept exactly when its trace is. A lower log ratio keeps a subset of the traced requests. A higher one also keeps requests that have no trace. Other trace samplers, or an upstream service that decides differently, can disagree.
- **Unsampled requests are held in memory until they end.** At most 200 lines are held, the newest kept, so a slow request with more loses its oldest lines.
- **4xx lines are always written**, because `request.client_error` is a `WARNING`. That includes scanners' 404s.
