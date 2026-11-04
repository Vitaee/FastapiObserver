"""Native cursor attempts are distinct from the existing completed DB totals."""

import asyncio
import logging

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from fastapiobserver.db_queries import count_queries
from fastapiobserver.experimental import observe_operation


def completion(caplog):
    return next(r.event for r in caplog.records if r.msg == "operation.completed")


def test_sql_failures_have_separate_counts_and_safe_detail(caplog):
    caplog.set_level(logging.INFO)
    engine = create_engine("sqlite://")
    count_queries(engine)
    count_queries(engine)
    try:
        with observe_operation("queries"):
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
                with pytest.raises(SQLAlchemyError):
                    connection.execute(text("SELECT * FROM private_missing_table"))
        event = completion(caplog)
        assert event["db"]["count"] == 1
        detail = event["db"]["sqlalchemy"]
        assert (detail["attempted"], detail["succeeded"], detail["failed"],
                detail["in_flight"]) == (2, 1, 1, 0)
        assert detail["failed_ms"] >= 0
        assert detail["errors"] == {"execution_error": 1}
        assert "private_missing_table" not in str(event)
    finally:
        engine.dispose()


def test_only_failed_sql_keeps_legacy_success_count_at_zero(caplog):
    caplog.set_level(logging.INFO)
    engine = create_engine("sqlite://")
    count_queries(engine)
    try:
        with observe_operation("failed.query"):
            with engine.connect() as connection:
                with pytest.raises(SQLAlchemyError):
                    connection.execute(text("SELECT * FROM absent_table"))
        event = completion(caplog)
        assert event["db"]["count"] == event["db"]["ms"] == 0
        assert event["db"]["sqlalchemy"]["failed"] == 1
    finally:
        engine.dispose()


def test_async_sql_uses_the_same_attempt_and_failure_layer(caplog):
    from sqlalchemy.ext.asyncio import create_async_engine
    caplog.set_level(logging.INFO)

    async def exercise():
        engine = create_async_engine("sqlite+aiosqlite://")
        count_queries(engine.sync_engine)
        try:
            with observe_operation("async.queries"):
                async with engine.connect() as connection:
                    await connection.execute(text("SELECT 1"))
                    with pytest.raises(SQLAlchemyError):
                        await connection.execute(text("SELECT * FROM absent_table"))
        finally:
            await engine.dispose()

    asyncio.run(exercise())
    detail = completion(caplog)["db"]["sqlalchemy"]
    assert (detail["attempted"], detail["succeeded"], detail["failed"]) == (2, 1, 1)


def test_integrity_errors_are_bounded_categories_not_exception_text(caplog):
    caplog.set_level(logging.INFO)
    engine = create_engine("sqlite://")
    count_queries(engine)
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))
        with observe_operation("duplicate"):
            with engine.begin() as connection:
                connection.execute(text("INSERT INTO users VALUES (1)"))
                with pytest.raises(SQLAlchemyError):
                    connection.execute(text("INSERT INTO users VALUES (1)"))
        detail = completion(caplog)["db"]["sqlalchemy"]
        assert detail["errors"] == {"integrity_error": 1}
        assert detail["attempted"] == detail["succeeded"] + detail["failed"] == 2
    finally:
        engine.dispose()


def test_connection_failures_are_not_reported_as_cursor_execution_attempts(caplog):
    import sqlite3
    caplog.set_level(logging.INFO)
    def unavailable():
        raise sqlite3.OperationalError("private connection failure")
    engine = create_engine("sqlite://", creator=unavailable)
    count_queries(engine)
    try:
        with observe_operation("connect"):
            with pytest.raises(SQLAlchemyError):
                engine.connect()
        event = completion(caplog)
        assert "db" not in event
        assert event["observation"] == {"coverage": "unknown"}
    finally:
        engine.dispose()
