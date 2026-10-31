# Design decisions

These records explain why fastapi-observer works the way it does. Each one says what was decided, what evidence backs it, and what it cost. Read the relevant one before changing that part of the library. If you think a decision is wrong, open an issue that argues against its evidence.

| # | Decision | In one line |
|---|---|---|
| [0001](0001-pure-asgi-middleware.md) | Pure ASGI middleware | `BaseHTTPMiddleware` adds about 153 µs per request and hides context the endpoint sets. |
| [0002](0002-logs-leave-through-a-queue.md) | Logs leave through a bounded queue | A slow sink costs 19 µs per log call instead of 1.3 ms. Under overload the oldest lines are dropped and counted. |
| [0003](0003-trust-only-known-peers.md) | Identity headers count only from known peers | Request ID, `traceparent` and client IP come only from addresses in `OBS_TRUSTED_CIDRS`. |
| [0004](0004-redact-before-anything-leaves.md) | Redact before anything leaves the process | No handler but ours on the root logger; it's the rule behind the 1.5.0 fix. |
| [0005](0005-route-templates-as-metric-labels.md) | Route templates as metric labels | One series per route, not per URL, with a known gap for unmatched paths. |
| [0006](0006-delegate-to-opentelemetry.md) | Hand work to OpenTelemetry and `prometheus_client` | 10% less code and seven bugs fixed, at the price of upstream changes reaching us. |
| [0007](0007-new-destinations-are-opt-in.md) | Nothing new receives data without being configured | Sinks from other packages, the control plane, metrics and OTLP export are all opt-in. |

## Postmortems

- [OTLP logs exported without redaction](../postmortems/2026-10-otlp-logs-exported-unredacted.md) (fixed in 1.5.0)

## Writing a new record

- Use the next number, and the same four parts: Context, Decision, Evidence, What it cost.
- Keep it to about one screen.
- Link code and tests at a release tag, not `main`, so the line numbers stay right.
- Put any measurement script in `examples/benchmarks/`, so the numbers can be reproduced.
