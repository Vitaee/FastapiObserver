"""Async database query counts, with optional OpenTelemetry SQL spans.

Run: uvicorn examples.db_tracing_app:app --reload
Try POST /users/ada followed by GET /users; inspect event.db in the JSON logs.
SQLite runs in memory by default. DATABASE_URL can select another async driver.
Set OBS_OTEL_ENABLED=true with the otel-sqlalchemy extra for supported SQLAlchemy
versions and a running collector. Query counts work with tracing disabled too.
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from sqlalchemy import String, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from fastapiobserver import ObservabilitySettings, install_observability

engine = create_async_engine(os.getenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:"))
sessions = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(128), unique=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield
    finally:
        await engine.dispose()


app = FastAPI(title="Database Observability Example", lifespan=lifespan)
install_observability(app, ObservabilitySettings(service="database"), db_engine=engine)


@app.post("/users/{username}")
async def create_user(username: str) -> dict[str, str | int]:
    async with sessions() as session:
        user = User(username=username)
        session.add(user)
        try:
            await session.commit()
        except IntegrityError:
            raise HTTPException(status_code=409, detail="Username already exists") from None
        return {"id": user.id, "username": user.username}


@app.get("/users")
async def get_users() -> list[dict[str, str | int]]:
    async with sessions() as session:
        users = await session.scalars(select(User).order_by(User.id))
        return [{"id": user.id, "username": user.username} for user in users]
