"""Pass-through pure ASGI middleware vs Starlette's BaseHTTPMiddleware.

Measures the cost each adds to a request before doing any work, and whether
the middleware can see a context variable the endpoint set. Backs decision
record 0001 (docs/decisions/0001-pure-asgi-middleware.md).

    python examples/benchmarks/middleware_overhead.py [requests] [rounds]
"""

from __future__ import annotations

import asyncio
import contextvars
import statistics
import sys
import time
from typing import Any

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import PlainTextResponse
from starlette.routing import Route

user: contextvars.ContextVar[str | None] = contextvars.ContextVar("user", default=None)
seen_after_endpoint: list[str | None] = []


async def endpoint(request: Any) -> PlainTextResponse:
    user.set("alice")  # what set_user_context() does
    return PlainTextResponse("ok")


class PureASGI:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        await self.app(scope, receive, send)
        if scope["type"] == "http":
            seen_after_endpoint.append(user.get())


class BaseHTTP(BaseHTTPMiddleware):
    async def dispatch(self, request: Any, call_next: Any) -> Any:
        response = await call_next(request)
        seen_after_endpoint.append(user.get())
        return response


def build(middleware: list[Middleware]) -> Starlette:
    return Starlette(routes=[Route("/", endpoint)], middleware=middleware)


SCOPE = {
    "type": "http", "method": "GET", "path": "/", "raw_path": b"/", "query_string": b"",
    "headers": [], "client": ("127.0.0.1", 1), "server": ("bench", 80), "scheme": "http",
    "http_version": "1.1", "root_path": "",
}


async def one(app: Starlette) -> None:
    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        pass

    await app(dict(SCOPE), receive, send)


async def per_request_us(app: Starlette, requests: int) -> float:
    for _ in range(500):
        await one(app)
    start = time.perf_counter()
    for _ in range(requests):
        await one(app)
    return (time.perf_counter() - start) / requests * 1e6


async def main(requests: int, rounds: int) -> None:
    apps = {
        "no middleware": build([]),
        "pure ASGI": build([Middleware(PureASGI)]),
        "BaseHTTPMiddleware": build([Middleware(BaseHTTP)]),
    }
    results: dict[str, list[float]] = {name: [] for name in apps}
    for _ in range(rounds):
        for name, app in apps.items():
            results[name].append(await per_request_us(app, requests))
    for name, values in results.items():
        print(f"{name:20s} median {statistics.median(values):6.1f} us per request")

    for name in ("pure ASGI", "BaseHTTPMiddleware"):
        seen_after_endpoint.clear()
        # A fresh context per request, as a server gives each connection.
        await asyncio.create_task(one(apps[name]), context=contextvars.Context())
        print(f"{name:20s} sees the endpoint's context variable: {seen_after_endpoint[0]!r}")


if __name__ == "__main__":
    args = [int(arg) for arg in sys.argv[1:3]]
    asyncio.run(main(*(args + [20_000, 5][len(args):])))
