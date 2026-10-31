from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from fastapiobserver.otel.lifecycle import close_on_app_shutdown


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


def test_close_on_app_shutdown_runs_once_per_app_and_provider() -> None:
    app = FastAPI()
    calls: list[str] = []
    provider = _Provider(calls)

    close_on_app_shutdown(app, provider, shutdown=True)
    close_on_app_shutdown(app, provider, shutdown=True)

    _run_lifespan(app)
    _run_lifespan(app)
    assert calls == ["shutdown"]


def test_close_on_app_shutdown_only_flushes_providers_we_do_not_own() -> None:
    app = FastAPI()
    calls: list[str] = []

    close_on_app_shutdown(app, _Provider(calls), shutdown=False)

    _run_lifespan(app)
    assert calls == ["flush"]


def test_close_on_app_shutdown_runs_after_custom_lifespan() -> None:
    events: list[str] = []

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
        events.append("startup")
        yield
        events.append("app-shutdown")

    app = FastAPI(lifespan=lifespan)
    close_on_app_shutdown(app, _Provider(events), shutdown=True)

    _run_lifespan(app)
    assert events == ["startup", "app-shutdown", "shutdown"]
