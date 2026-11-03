"""One demo app for all three services; /lab routes deliberately trigger failures."""

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Annotated, Literal

import httpx2
from fastapi import FastAPI, HTTPException, Query
from sqlalchemy import create_engine, text
from sqlalchemy.pool import QueuePool

from fastapiobserver import (
    ObservabilitySettings,
    get_request_id,
    get_span_id,
    get_trace_id,
    inject_trace_headers,
    install_observability,
)

settings = ObservabilitySettings.from_env()
logger = logging.getLogger(settings.service)
TARGET_B = os.getenv("TARGET_B", "http://localhost:8001")
TARGET_C = os.getenv("TARGET_C", "http://localhost:8002")
Delay = Annotated[float, Query(gt=0, le=3)]

# A single local connection makes pool pressure reproducible without another service.
engine = create_engine(
    "sqlite://",
    poolclass=QueuePool,
    pool_size=1,
    max_overflow=0,
    connect_args={"check_same_thread": False},
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE lab_items (id INTEGER PRIMARY KEY, name TEXT)"))
        connection.execute(
            text("INSERT INTO lab_items (id, name) VALUES (:id, :name)"),
            [{"id": i, "name": f"Item {i}"} for i in range(1, 11)],
        )
    try:
        yield
    finally:
        engine.dispose()


app = FastAPI(title=f"{settings.service} — Observability Demo", lifespan=lifespan)
install_observability(app, settings, db_engine=engine, db_commenter_enabled=False)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": settings.service}


@app.get("/items/{item_id}")
def get_item(item_id: int) -> dict:
    logger.info("item.lookup", extra={"event": {"item_id": item_id}})
    return {
        "item_id": item_id,
        "service": settings.service,
        "request_id": get_request_id(),
        "trace_id": get_trace_id(),
        "span_id": get_span_id(),
    }


@app.get("/users/{user_id}")
def get_user(user_id: int) -> dict:
    return {"user_id": user_id, "service": settings.service, "request_id": get_request_id()}


@app.get("/slow")
async def slow_endpoint(seconds: Delay = 0.5) -> dict:
    """Healthy I/O wait: the loop remains available to serve other requests."""
    await asyncio.sleep(seconds)
    return {"delay_seconds": seconds, "service": settings.service}


@app.get("/error")
def error_endpoint(status: Literal["400", "404", "500", "503"] = "500") -> None:
    raise HTTPException(status_code=int(status), detail="Intentional demo error")


async def _fetch_json(client: httpx2.AsyncClient, url: str) -> object:
    try:
        response = await client.get(url, headers=inject_trace_headers())
        response.raise_for_status()
        return response.json()
    except (httpx2.HTTPError, ValueError) as exc:
        return {"error": str(exc)}


@app.get("/chain")
async def chain() -> dict:
    """Forward request IDs and the current trace context to both downstreams."""
    async with httpx2.AsyncClient(timeout=5) as client:
        results = await asyncio.gather(
            _fetch_json(client, f"{TARGET_B}/items/1"),
            _fetch_json(client, f"{TARGET_C}/users/1"),
        )
    return {
        "source": settings.service,
        "request_id": get_request_id(),
        "trace_id": get_trace_id(),
        "results": dict(zip(("app_b", "app_c"), results)),
    }


@app.get("/lab/block")
async def block_event_loop(seconds: Delay = 0.5) -> dict:
    """Intentional bug: sync sleep blocks every request on this worker. See /slow."""
    time.sleep(seconds)
    return {"blocked_seconds": seconds}


@app.get("/lab/db-pool")
async def hold_db_connection(seconds: Delay = 2) -> dict:
    """Intentional bug: hold the only connection while waiting on unrelated I/O."""
    with engine.connect():
        await asyncio.sleep(seconds)
    return {"held_seconds": seconds}


@app.get("/lab/n-plus-one")
def n_plus_one() -> list[str]:
    """Intentional bug: one ID query followed by one lookup per item."""
    with engine.connect() as connection:
        ids = connection.execute(text("SELECT id FROM lab_items ORDER BY id")).scalars().all()
        return [
            connection.execute(
                text("SELECT name FROM lab_items WHERE id = :id"), {"id": item_id}
            ).scalar_one()
            for item_id in ids
        ]


@app.get("/lab/batched")
def batched() -> list[str]:
    """The same ten results in one query."""
    with engine.connect() as connection:
        return list(connection.execute(text("SELECT name FROM lab_items ORDER BY id")).scalars())
