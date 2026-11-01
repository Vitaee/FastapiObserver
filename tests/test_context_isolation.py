"""Request context must not leak between requests that run at the same time."""

from __future__ import annotations

import asyncio
import random

import httpx2
from fastapi import FastAPI

from fastapiobserver import (
    ObservabilitySettings,
    get_request_id,
    install_observability,
    set_user_context,
)
from fastapiobserver.logging import flush_logging
from fastapiobserver.request_context import get_user_context

_REQUESTS = 200


def _app() -> FastAPI:
    app = FastAPI()

    @app.get("/async/{n}")
    async def async_endpoint(n: int) -> dict[str, object]:
        set_user_context({"n": n})
        await asyncio.sleep(random.random() / 100)  # let other requests run in between
        return {"request_id": get_request_id(), "user": get_user_context()}

    @app.get("/sync/{n}")
    def sync_endpoint(n: int) -> dict[str, object]:
        set_user_context({"n": n})
        return {"request_id": get_request_id(), "user": get_user_context()}

    install_observability(app, ObservabilitySettings(), metrics_enabled=False)
    return app


def test_concurrent_requests_see_only_their_own_context() -> None:
    app = _app()

    async def main() -> list[tuple[int, str, httpx2.Response]]:
        transport = httpx2.ASGITransport(app=app)  # client 127.0.0.1: a trusted proxy
        async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:

            async def call(n: int) -> tuple[int, str, httpx2.Response]:
                request_id = f"req-{n}"
                kind = "async" if n % 2 else "sync"
                response = await client.get(f"/{kind}/{n}", headers={"x-request-id": request_id})
                return n, request_id, response

            return await asyncio.gather(*(call(n) for n in range(_REQUESTS)))

    results = asyncio.run(main())
    flush_logging()  # before pytest closes the captured stdout
    for n, request_id, response in results:
        assert response.json() == {"request_id": request_id, "user": {"n": n}}
        assert response.headers["x-request-id"] == request_id
    assert get_request_id() is None  # nothing left behind outside a request
