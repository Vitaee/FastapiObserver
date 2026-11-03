from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.testclient import TestClient

from fastapiobserver import ObservabilitySettings, install_observability, observability_lifespan
from fastapiobserver.logging import setup_logging, shutdown_logging


class _Collector(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _block_the_loop() -> None:
    time.sleep(0.4)


def _add_blocking_route(app: FastAPI) -> None:
    @app.get("/blocking")
    async def blocking() -> dict[str, str]:
        _block_the_loop()
        return {}


def _loop_blocked_records(app: FastAPI, *paths: str) -> list[logging.LogRecord]:
    settings = ObservabilitySettings(loop_block_threshold_ms=100)
    install_observability(app, settings, metrics_enabled=False)
    collector = _Collector()
    setup_logging(settings, logs_mode="otlp", extra_handlers=[collector])
    with TestClient(app) as client:  # the watchdog runs during the lifespan
        for path in paths:
            client.get(path)
    shutdown_logging()
    return [r for r in collector.records if r.getMessage() == "event_loop.blocked"]


def test_blocking_call_in_async_endpoint_is_logged_with_its_stack() -> None:
    app = FastAPI()
    _add_blocking_route(app)

    @app.get("/awaiting")
    async def awaiting() -> dict[str, str]:
        await asyncio.sleep(0.4)
        return {}

    [record] = _loop_blocked_records(app, "/awaiting", "/blocking")
    assert record.levelno == logging.WARNING
    assert record.event["blocked_ms"] >= 100  # type: ignore[attr-defined]
    assert "_block_the_loop" in record.event["stack"]  # type: ignore[attr-defined]


def test_observability_lifespan_in_the_apps_own_lifespan_reports_a_block_once() -> None:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with observability_lifespan(app):  # inside the one install_observability() adds
            yield

    app = FastAPI(lifespan=lifespan)
    _add_blocking_route(app)

    assert len(_loop_blocked_records(app, "/blocking")) == 1
