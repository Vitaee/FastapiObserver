from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from fastapiobserver.otel.lifecycle import flush_on_app_shutdown


class _Provider:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def force_flush(self) -> None:
        self.calls.append("flush")

    def shutdown(self) -> None:
        self.calls.append("shutdown")


def _run_lifespan(app: FastAPI) -> None:
    async def _cycle() -> None:
        async with app.router.lifespan_context(app):
            pass

    asyncio.run(_cycle())


def test_flush_on_app_shutdown_flushes_each_lifespan_and_never_shuts_down() -> None:
    # The provider is global, and the app may start again, as a reused TestClient
    # does. Process exit shuts it down through the SDK's own atexit hook.
    app = FastAPI()
    calls: list[str] = []
    provider = _Provider(calls)

    flush_on_app_shutdown(app, provider)
    flush_on_app_shutdown(app, provider)

    _run_lifespan(app)
    _run_lifespan(app)
    assert calls == ["flush", "flush"]


def test_flush_on_app_shutdown_runs_after_custom_lifespan() -> None:
    events: list[str] = []

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
        events.append("startup")
        yield
        events.append("app-shutdown")

    app = FastAPI(lifespan=lifespan)
    flush_on_app_shutdown(app, _Provider(events))

    _run_lifespan(app)
    assert events == ["startup", "app-shutdown", "flush"]
