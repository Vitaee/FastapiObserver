from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from fastapiobserver.otel.lifecycle import (
    build_provider_shutdown_callback,
    register_shutdown_hook,
)


def _run_lifespan(app: FastAPI) -> None:
    async def _cycle() -> None:
        async with app.router.lifespan_context(app):
            pass

    asyncio.run(_cycle())


def test_register_shutdown_hook_is_idempotent_per_app() -> None:
    app = FastAPI()
    calls: list[str] = []
    key = f"otel.test.{uuid.uuid4()}"

    register_shutdown_hook(key, lambda: calls.append("first"), app=app)
    register_shutdown_hook(key, lambda: calls.append("second"), app=app)

    _run_lifespan(app)
    _run_lifespan(app)
    assert calls == ["first"]


def test_register_shutdown_hook_runs_with_custom_lifespan() -> None:
    events: list[str] = []

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
        events.append("startup")
        yield
        events.append("shutdown")

    app = FastAPI(lifespan=lifespan)
    register_shutdown_hook(f"otel.test.{uuid.uuid4()}", lambda: events.append("hook"), app=app)

    _run_lifespan(app)
    assert events == ["startup", "shutdown", "hook"]


def test_build_provider_shutdown_callback_flush_only() -> None:
    class _Provider:
        def __init__(self) -> None:
            self.flush_calls = 0
            self.shutdown_calls = 0

        def force_flush(self) -> None:
            self.flush_calls += 1

        def shutdown(self) -> None:
            self.shutdown_calls += 1

    provider = _Provider()
    callback = build_provider_shutdown_callback(
        provider,
        component="logger_provider",
        shutdown=False,
    )
    callback()

    assert provider.flush_calls == 1
    assert provider.shutdown_calls == 0


def test_build_provider_shutdown_callback_flush_and_shutdown() -> None:
    class _Provider:
        def __init__(self) -> None:
            self.flush_calls = 0
            self.shutdown_calls = 0

        def force_flush(self) -> None:
            self.flush_calls += 1

        def shutdown(self) -> None:
            self.shutdown_calls += 1

    provider = _Provider()
    callback = build_provider_shutdown_callback(
        provider,
        component="tracer_provider",
        shutdown=True,
    )
    callback()

    assert provider.flush_calls == 1
    assert provider.shutdown_calls == 1
