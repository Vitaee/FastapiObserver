"""Budget assertions observe the same native cursor-entry hook as diagnostics."""

from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
import importlib

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from fastapiobserver.db_queries import count_queries
from fastapiobserver.experimental import observe_operation


def budget(engine, maximum):
    return importlib.import_module("fastapiobserver.testing").assert_query_budget(engine, maximum)


def test_native_budget_counts_successes_failures_and_nested_operations():
    engine = create_engine("sqlite://")
    count_queries(engine)
    try:
        with budget(engine, 2) as observed:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
                with observe_operation("child"):
                    with pytest.raises(SQLAlchemyError):
                        connection.execute(text("SELECT * FROM absent_table"))
        assert observed.count == 2
    finally:
        engine.dispose()


def test_budget_failure_contains_counts_without_sql_or_credentials():
    engine = create_engine("sqlite://")
    count_queries(engine)
    try:
        with pytest.raises(AssertionError, match="at most 0.*observed 1") as caught:
            with budget(engine, 0):
                with engine.connect() as connection:
                    connection.execute(text("SELECT 'private-value'"))
        assert "private-value" not in str(caught.value)
    finally:
        engine.dispose()


def test_budget_refuses_unattached_native_hooks_instead_of_passing_zero():
    engine = create_engine("sqlite://")
    try:
        with pytest.raises(RuntimeError, match="count_queries"):
            with budget(engine, 0):
                pass
        count_queries(engine)
        with budget(engine, 0) as observed:
            pass
        assert observed.count == 0
    finally:
        engine.dispose()


def test_nested_budgets_select_engines_and_ignore_manual_totals():
    first, second = create_engine("sqlite://"), create_engine("sqlite://")
    count_queries(first)
    count_queries(second)
    try:
        with budget(first, 1) as outer:
            with budget(second, 1) as inner, observe_operation("manual") as operation:
                operation.record_call("db", 999)
                with first.connect() as connection:
                    connection.execute(text("SELECT 1"))
                with second.connect() as connection:
                    connection.execute(text("SELECT 2"))
        assert outer.count == inner.count == 1
    finally:
        first.dispose()
        second.dispose()


def test_budget_preserves_the_application_exception_and_rejects_reentry():
    engine = create_engine("sqlite://")
    count_queries(engine)
    observed = budget(engine, 0)
    original = ValueError("original")
    try:
        with pytest.raises(ValueError) as caught:
            with observed:
                with engine.connect() as connection:
                    connection.execute(text("SELECT 1"))
                raise original
        assert caught.value is original
        with pytest.raises(RuntimeError):
            with observed:
                pass
    finally:
        engine.dispose()


def test_threaded_budget_uses_copied_context_and_closes_retained_callbacks():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    count_queries(engine)
    def execute():
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    try:
        with budget(engine, 16) as observed, ThreadPoolExecutor(max_workers=4) as executor:
            retained = copy_context()
            futures = [executor.submit(copy_context().run, execute) for _ in range(16)]
            for future in futures:
                future.result()
        retained.run(execute)
        assert observed.count == 16
    finally:
        engine.dispose()


@pytest.mark.parametrize("maximum", [-1, True, 1.5, "2"])
def test_budget_rejects_invalid_limits(maximum):
    engine = create_engine("sqlite://")
    try:
        with pytest.raises((TypeError, ValueError)):
            budget(engine, maximum)
    finally:
        engine.dispose()


def test_async_engine_budget_is_independent_of_log_sampling():
    import asyncio
    from sqlalchemy.ext.asyncio import create_async_engine
    from fastapiobserver import ObservabilitySettings, setup_logging
    setup_logging(ObservabilitySettings(log_level="CRITICAL", log_sample_rate=0))
    async def exercise():
        engine = create_async_engine("sqlite+aiosqlite://")
        count_queries(engine.sync_engine)
        try:
            with budget(engine, 1) as observed, observe_operation("not.logged"):
                async with engine.connect() as connection:
                    await connection.execute(text("SELECT 1"))
            assert observed.count == 1
        finally:
            await engine.dispose()
    asyncio.run(exercise())


def test_detaching_native_hooks_cannot_produce_a_false_passing_budget():
    from sqlalchemy import event
    from fastapiobserver.db_queries import _before
    engine = create_engine("sqlite://")
    count_queries(engine)
    try:
        with pytest.raises(RuntimeError, match="counting changed"):
            with budget(engine, 0):
                event.remove(engine, "before_cursor_execute", _before)
                with engine.connect() as connection:
                    connection.execute(text("SELECT 1"))
    finally:
        engine.dispose()


def test_query_budget_example_needs_no_framework_or_exporter():
    import os
    from pathlib import Path
    import subprocess
    import sys
    import fastapiobserver
    root = Path(__file__).resolve().parents[1]
    script = """
import importlib.abc,runpy,sys
class Forbidden(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,path=None,target=None):
        if fullname.split('.')[0] in {'fastapi','starlette','opentelemetry'}:
            raise AssertionError('Unexpected import: '+fullname)
sys.meta_path.insert(0,Forbidden())
runpy.run_path(sys.argv[1],run_name='__main__')
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(root / "examples/query_budget.py")],
        capture_output=True, text=True, timeout=15,
        env={**os.environ, "PYTHONPATH": str(Path(fastapiobserver.__file__).resolve().parents[1])},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "2 native cursor attempts, including one failed execution", "Over-budget query detected",
    ]


def test_budget_observes_native_queries_in_a_real_fastapi_request():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from fastapiobserver import install_observability
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    app = FastAPI()
    @app.get("/query")
    def query():
        with engine.connect() as connection:
            return {"result": connection.execute(text("SELECT 42")).scalar_one()}
    install_observability(app, db_engine=engine, metrics_enabled=False, configure_logging=False)
    try:
        with TestClient(app) as client, budget(engine, 1) as observed:
            assert client.get("/query").json() == {"result": 42}
        assert observed.count == 1
    finally:
        engine.dispose()
