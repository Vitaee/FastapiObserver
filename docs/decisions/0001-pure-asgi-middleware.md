# 0001: Pure ASGI middleware, not `BaseHTTPMiddleware`

**Status:** accepted, since 0.1.0 · **Reviewed:** 2026-10-31

## Context

The request middleware runs on every request. It has to see the status code, the duration, the matched route, and context the endpoint sets during the request, such as `set_user_context()`. Starlette's `BaseHTTPMiddleware`, with its `dispatch(request, call_next)`, is the easy way to write one.

## Decision

`RequestLoggingMiddleware` is a plain ASGI callable ([`request_logging.py:47`](https://github.com/Vitaee/FastapiObserver/blob/v1.8.0/src/fastapiobserver/middleware/request_logging.py#L47)). It reads headers from the raw scope bytes and wraps `send` to see the response status.

## Evidence

Each middleware passes the request through and does nothing else. Measured with [`examples/benchmarks/middleware_overhead.py`](https://github.com/Vitaee/FastapiObserver/blob/main/examples/benchmarks/middleware_overhead.py): Starlette 1.10.0, Python 3.14.7, Apple silicon, 20,000 in-process requests × 5 rounds, median shown.

| | Per request |
|---|---:|
| No middleware | 5.6 µs |
| Pure ASGI | 5.7 µs |
| `BaseHTTPMiddleware` | 158.4 µs |

`BaseHTTPMiddleware` adds about 153 µs before doing any work. That's more than three times fastapi-observer's whole default cost per request, which is about 43 µs (see [benchmarks](../benchmarks.md)).

The same script checks context visibility. An async endpoint sets a context variable, and each request runs in a fresh context. Afterwards, pure ASGI middleware sees the value and `BaseHTTPMiddleware` sees `None`, because it runs the endpoint in a separate task. That's why `user_context` set in an async endpoint appears on the `request.completed` line.

## What it cost

- **Harder code to read.** Wrapping `send` and reading raw header bytes is harder to follow than `Request` and `Response` objects.
- **A limit it doesn't fix.** Sync (`def`) endpoints run in a thread pool with a copy of the context, so `set_user_context()` inside one doesn't reach `request.completed`. Fixing that would need a mutable holder that the middleware creates and the endpoint fills in.
