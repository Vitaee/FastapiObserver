from __future__ import annotations

import asyncio
import inspect
import uuid

from fastapi import FastAPI

from fastapiobserver.otel.lifecycle import (
    build_provider_shutdown_callback,
    register_shutdown_hook,
)


def _run_shutdown_handlers(app: FastAPI) -> None:
    for handler in app.router.on_shutdown:
        result = handler()
        if inspect.isawaitable(result):
            asyncio.run(result)


def test_register_shutdown_hook_is_idempotent_per_app() -> None:
    app = FastAPI()
    calls: list[str] = []
    key = f"otel.test.{uuid.uuid4()}"

    register_shutdown_hook(key, lambda: calls.append("first"), app=app)
    register_shutdown_hook(key, lambda: calls.append("second"), app=app)

    assert len(app.router.on_shutdown) == 1

    _run_shutdown_handlers(app)
    _run_shutdown_handlers(app)
    assert calls == ["first"]


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
