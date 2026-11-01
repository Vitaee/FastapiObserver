# Design decisions

Each record says what was decided, the evidence for it and what it cost. Read the matching one before changing that part of the library. To challenge a decision, open an issue that argues with its evidence.

| # | Decision | In one line |
|---|---|---|
| [0001](0001-pure-asgi-middleware.md) | Pure ASGI middleware | `BaseHTTPMiddleware` adds about 153 µs per request and hides context the endpoint sets. |
| [0002](0002-logs-leave-through-a-queue.md) | Logs leave through a bounded queue | With a slow handler, logging costs the caller about 19 µs per call instead of 1.3 to 1.5 ms. Under overload the oldest lines are dropped and counted. |
| [0003](0003-trust-only-known-peers.md) | Identity headers count only from known peers | Request ID, `traceparent` and client IP come only from addresses in `OBS_TRUSTED_CIDRS`. |
| [0004](0004-redact-before-anything-leaves.md) | Redact before anything leaves the process | No handler but ours on the root logger. Written after the 1.5.0 leak. |
| [0005](0005-route-templates-as-metric-labels.md) | Route templates as metric labels | One series per route, not per URL, and scanner 404s share one `<unmatched>` label. |
| [0006](0006-delegate-to-opentelemetry.md) | Hand work to OpenTelemetry and `prometheus_client` | Use them for what they already do, and accept that their changes reach us. |
| [0007](0007-new-destinations-are-opt-in.md) | New destinations and controls are opt-in | Sinks from other packages, the control plane, metrics and OTLP export stay off until configured. |
| [0008](0008-one-state-object-per-request.md) | One state object per request | Held log lines, user context, query totals and log bytes reach the middleware from sync endpoints and span processors. Costs about 2 µs per request. |
| [0009](0009-sample-logs-per-request-by-trace-id.md) | Sample logs per request, by trace ID | A request's `INFO` lines are kept or dropped together, the same way as its trace in every service. Failed and slow requests are always kept. |
| [0010](0010-trace-with-fastapis-own-opentelemetry.md) | Trace with FastAPI's own OpenTelemetry | On FastAPI 0.142+, FastAPI traces requests itself, and its own log export stays off so error logs go through redaction. |
| [0011](0011-every-unmatched-request-shares-one-label.md) | Every unmatched request shares one label | Preflights, rejected requests and scanners share `<unmatched>`, and made-up methods share `_OTHER`. |

## Writing a new record

- Use the next number, and the same four parts: Context, Decision, Evidence, What it cost.
- Keep it to about one screen.
- Link code and tests at a release tag once one contains them, and at `main` until then.
- Put any measurement script in `examples/benchmarks/`, so the numbers can be reproduced.
- When a decision changes, write a new record that supersedes the old one, and mark the old one superseded. Don't rewrite it.
