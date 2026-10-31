from __future__ import annotations

import asyncio
import logging
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

from fastapiobserver import ObservabilitySettings, install_observability
from fastapiobserver.logging import setup_logging, shutdown_logging


class _Collector(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _block_the_loop() -> None:
    time.sleep(0.4)


def test_blocking_call_in_async_endpoint_is_logged_with_its_stack() -> None:
    settings = ObservabilitySettings(loop_block_threshold_ms=100)
    app = FastAPI()

    @app.get("/blocking")
    async def blocking() -> dict[str, str]:
        _block_the_loop()
        return {}

    @app.get("/awaiting")
    async def awaiting() -> dict[str, str]:
        await asyncio.sleep(0.4)
        return {}

    install_observability(app, settings, metrics_enabled=False)
    collector = _Collector()
    setup_logging(settings, logs_mode="otlp", extra_handlers=[collector])
    with TestClient(app) as client:  # the watchdog runs during the lifespan
        client.get("/awaiting")
        client.get("/blocking")
    shutdown_logging()

    [record] = [r for r in collector.records if r.getMessage() == "event_loop.blocked"]
    assert record.levelno == logging.WARNING
    assert record.event["blocked_ms"] >= 100  # type: ignore[attr-defined]
    assert "_block_the_loop" in record.event["stack"]  # type: ignore[attr-defined]
