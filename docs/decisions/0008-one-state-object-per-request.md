# 0008: One state object per request

**Status:** accepted · **Reviewed:** 2026-10-31

## Context

Three features add to a request while it runs, and the middleware reads the result when the request ends:

- holding a request's lower-level log lines until it's known whether it failed (`LOG_LEVEL_ON_ERROR`);
- counting its database queries and outbound HTTP calls, and spotting one query repeated per item (N+1);
- labelling each log line with its route for `fastapiobserver_log_bytes_total`.

The code that adds runs elsewhere: a sync endpoint in the thread pool, an OpenTelemetry span processor, a log filter. A context variable *set* there doesn't reach the middleware, because a sync endpoint runs in a copy of the context. That's also why `set_user_context()` in a sync endpoint used to be lost ([0001](0001-pure-asgi-middleware.md)). It now stores its value on the same object.

## Decision

- The middleware creates one `RequestState` per request and stores it in a context variable ([`request_context.py`](https://github.com/Vitaee/FastapiObserver/blob/main/src/fastapiobserver/request_context.py)). A copy of the context still holds the same object, so what a sync endpoint, a span processor or a log filter adds reaches the middleware.
- The middleware reads it in `_finalize_request()` and clears it.
- Each feature stays off until configured; only creating the object is always paid:
    - held lines need `LOG_LEVEL_ON_ERROR`;
    - query and call totals come from OpenTelemetry client spans, so they need tracing and instrumented clients ([0006](0006-delegate-to-opentelemetry.md));
    - log bytes are counted only once the Prometheus collector that exports them is registered.
- The event-loop watchdog doesn't use it. It runs on its own thread, started and stopped by the lifespan.

## Evidence

CPU per request, log line included, from [`examples/benchmarks/request_cpu.py`](https://github.com/Vitaee/FastapiObserver/blob/main/examples/benchmarks/request_cpu.py) on an Apple M1, median of alternating runs:

| | 1.9.0 | With this change |
|---|---:|---:|
| Default settings (8 runs each) | 48.9 µs | 50.6 µs (+1.7) |
| Prometheus metrics on, so log bytes are counted (5 runs each) | 55.5 µs | 57.7 µs (+2.2) |

Turning off any single step saved at most 0.4 µs; the cost is spread over creating, reading and clearing the object.

Tests in [`test_request_insights.py`](https://github.com/Vitaee/FastapiObserver/blob/main/tests/test_request_insights.py) cover lines held in a sync endpoint, user context set in a sync endpoint, totals from client spans, and bytes counted once across two sinks. [`test_loop_watchdog.py`](https://github.com/Vitaee/FastapiObserver/blob/main/tests/test_loop_watchdog.py) blocks a real endpoint.

## What it cost

- **About 2 µs per request**, with every feature off.
- **Holding lines creates them.** With `LOG_LEVEL_ON_ERROR=DEBUG`, the root logger is at `DEBUG`, so every `DEBUG` call in the process builds a record, and other handlers on the root logger receive it. Loggers you set below `LOG_LEVEL` yourself are held too.
- **Totals only for sampled requests.** Unsampled spans never reach a span processor, so at a trace sampling ratio of 0.1, nine in ten request lines have no `db` or `http` field.
- **Fixed limits.** 200 held lines per request, newest kept. An N+1 warning at 10 runs of one statement, written once per method, route and statement in each process.
- **No log bytes in Prometheus multiprocess mode**, like the other log-queue metrics.
