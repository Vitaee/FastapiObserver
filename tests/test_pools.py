from __future__ import annotations

import asyncio
import logging
import threading
from types import SimpleNamespace

import anyio.to_thread
from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx2
import pytest

from fastapiobserver import ObservabilitySettings, install_observability
from fastapiobserver import pools

prometheus_client = pytest.importorskip("prometheus_client")


@pytest.fixture(autouse=True)
def _fresh_warnings() -> None:
    pools._last_warned.clear()


def _warnings(caplog: pytest.LogCaptureFixture, message: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.getMessage() == message]


def test_requests_waiting_for_a_thread_are_reported(caplog: pytest.LogCaptureFixture) -> None:
    app = FastAPI()
    release = threading.Event()

    @app.get("/sync")
    def sync_endpoint() -> dict[str, str]:  # runs in AnyIO's thread pool
        release.wait(5)
        return {}

    @app.get("/ping")
    async def ping() -> dict[str, str]:
        return {}

    install_observability(app, ObservabilitySettings(), metrics_enabled=True)

    async def main() -> str:
        async with app.router.lifespan_context(app):
            anyio.to_thread.current_default_thread_limiter().total_tokens = 1
            transport = httpx2.ASGITransport(app=app)
            async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
                blocked = [asyncio.create_task(client.get("/sync")) for _ in range(2)]
                await asyncio.sleep(0.1)  # one call holds the only thread, one waits
                await client.get("/ping")  # starts while the pool is full
                await client.get("/ping")  # within a minute: no second warning
                metrics = prometheus_client.generate_latest().decode()
                release.set()
                await asyncio.gather(*blocked)
        return metrics

    with caplog.at_level(logging.WARNING, logger="fastapiobserver.pools"):
        metrics = asyncio.run(main())

    [warning] = _warnings(caplog, "threadpool.saturated")
    assert warning.event == {"busy": 1, "limit": 1, "waiting": 1}  # type: ignore[attr-defined]
    assert "fastapiobserver_threadpool_busy 1.0" in metrics
    assert "fastapiobserver_threadpool_limit 1.0" in metrics
    assert "fastapiobserver_threadpool_waiting 1.0" in metrics


class _QueuePool:
    """The part of SQLAlchemy's QueuePool that fastapi-observer reads."""

    def __init__(self, size: int, max_overflow: int) -> None:
        self._size = size
        self._max_overflow = max_overflow
        self.checked_out = 0

    def size(self) -> int:
        return self._size

    def checkedout(self) -> int:
        return self.checked_out


class _Engine:
    """A SQLAlchemy engine: weakly referenceable, with a pool and a URL."""

    def __init__(self, pool: _QueuePool) -> None:
        self.pool = pool
        self.url = SimpleNamespace(render_as_string=lambda hide_password: "postgresql://app@db/orders")


def test_a_full_database_pool_is_reported(caplog: pytest.LogCaptureFixture) -> None:
    pool = _QueuePool(size=2, max_overflow=1)
    engine = _Engine(pool)
    app = FastAPI()

    @app.get("/ping")
    async def ping() -> dict[str, str]:
        return {}

    install_observability(app, ObservabilitySettings(), metrics_enabled=True, db_engine=engine)
    client = TestClient(app)
    with caplog.at_level(logging.WARNING, logger="fastapiobserver.pools"):
        client.get("/ping")  # 0 of 3 checked out
        pool.checked_out = 3
        client.get("/ping")
        client.get("/ping")  # within a minute: no second warning

    [warning] = _warnings(caplog, "db.pool_exhausted")
    assert warning.event == {  # type: ignore[attr-defined]
        "engine": "postgresql://app@db/orders",
        "checked_out": 3,
        "limit": 3,
    }
    metrics = prometheus_client.generate_latest().decode()
    assert 'fastapiobserver_db_pool_checked_out{engine="postgresql://app@db/orders"} 3.0' in metrics
    assert 'fastapiobserver_db_pool_limit{engine="postgresql://app@db/orders"} 3.0' in metrics
