"""
db_tracing_app.py — Demonstrates SQLCommenter and Database Tracing Integration

Prerequisites:
    pip install "fastapi-observer[otel,otel-sqlalchemy]"
    pip install sqlalchemy aiosqlite

Run this:
    OTEL_ENABLED=true uvicorn examples.db_tracing_app:app --reload

What happens under the hood:
    1. The SQLAlchemy `create_async_engine` is dynamically instrumented by OpenTelemetry.
    2. Every database query generates an OTel span linked to the current HTTP Request.
    3. The executing query automatically receives a SQL comment containing the `traceparent` and
       `route` info.

    Example executed query:
    SELECT * FROM users /*traceparent='00-d8...,route='/users',db_driver='sqlite'*/
"""

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from sqlalchemy import Column, Integer, String, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.declarative import declarative_base

from fastapiobserver import (
    ObservabilitySettings,
    install_observability,
)
import os
from fastapiobserver.otel import OTelSettings

# 1. Setup SQLAlchemy Async Engine & Base
# To test with external DBs mapped in docker-compose, pass the async driver:
#   PostgreSQL: DATABASE_URL="postgresql+asyncpg://user:pass@localhost:5432/db"
#   MySQL:      DATABASE_URL="mysql+aiomysql://user:pass@localhost:3306/db"
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
engine = create_async_engine(DATABASE_URL, echo=True)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)

# 2. Application Setup
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # Create tables on startup
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield

app = FastAPI(title="Database Tracing Example", lifespan=lifespan)
logger = logging.getLogger("examples.db_tracing_app")

settings = ObservabilitySettings(
    app_name="data-api",
    service="data",
    environment="development",
)

otel_settings = OTelSettings(
    enabled=True,
    service_name="data-api",
)

# 3. Instrument the Engine
# Passing `db_engine` dynamically patches SQLAlchemy queries to include
# traces and SQLCommenter comments.
install_observability(
    app,
    settings,
    otel_settings=otel_settings,
    db_engine=engine,
    db_commenter_options={
        "opentelemetry_values": True,
        "route": True,
        "db_driver": True,
    }
)

@app.post("/users/{username}")
async def create_user(username: str) -> dict[str, str | int]:
    """
    Creates a user. Check the terminal logs: you will see the generated INSERT SQL 
    contains a traceparent!
    """
    async with AsyncSessionLocal() as session:
        user = User(username=username)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return {"id": user.id, "username": user.username}

@app.get("/users")
async def get_users() -> list[dict[str, str | int]]:
    """
    Fetches all users. The SELECT statement will also be intercepted and commented
    with the current trace structure.
    """
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User))
        users = result.scalars().all()
        return [{"id": u.id, "username": u.username} for u in users]
