"""Actual optional instrumentor and SQLite operations; also run from the wheel."""

import asyncio
import importlib.metadata
import json
import logging
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fastapiobserver import ObservabilitySettings, OTelSettings, install_observability
from fastapiobserver.db_tracing import uninstrument_sqlalchemy
from fastapiobserver.logging import setup_logging, shutdown_logging


@pytest.mark.parametrize("asynchronous", [False, True])
def test_real_sql_spans_and_query_totals_share_the_server_trace(asynchronous, monkeypatch):
    pytest.importorskip("opentelemetry.instrumentation.sqlalchemy")
    from sqlalchemy import create_engine, event, text
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    import fastapiobserver.otel.tracing as tracing

    exporter = InMemorySpanExporter()
    monkeypatch.setattr(tracing, "build_otlp_exporter", lambda *_: exporter)
    uninstrument_sqlalchemy()
    engines = [create_async_engine("sqlite+aiosqlite://", poolclass=NullPool)
               if asynchronous else create_engine("sqlite://", poolclass=NullPool)
               for _ in range(2)]
    connections = {"opened": 0, "closed": 0}

    def opened(*_):
        connections["opened"] += 1

    def closed(*_):
        connections["closed"] += 1

    for engine in engines:
        pool = engine.sync_engine.pool if asynchronous else engine.pool
        event.listen(pool, "connect", opened)
        event.listen(pool, "close", closed)
    app = FastAPI()

    @app.get("/queries")
    async def queries():
        for engine in engines:
            if asynchronous:
                async with engine.connect() as connection:
                    for number in range(6):
                        await connection.execute(text("SELECT :number"), {"number": number})
            else:
                with engine.connect() as connection:
                    for number in range(6):
                        connection.execute(text("SELECT :number"), {"number": number})
        return {"ok": True}

    class Collector(logging.Handler):
        def emit(self, record):
            if record.getMessage() == "request.completed":
                self.payload = json.loads(self.format(record))

    collector = Collector()
    install_observability(
        app, ObservabilitySettings(), db_engine=engines, db_commenter_enabled=False,
        otel_settings=OTelSettings(enabled=True), metrics_enabled=False,
    )
    setup_logging(ObservabilitySettings(), logs_mode="otlp", extra_handlers=[collector])
    try:
        with TestClient(app) as client:
            response = client.get("/queries")
        shutdown_logging()
        assert response.status_code == 200
        assert collector.payload["event"]["db"]["count"] == 12
        spans = exporter.get_finished_spans()
        queries = [s for s in spans if s.attributes.get("db.statement", "").startswith("SELECT")]
        servers = [s for s in spans if s.kind.name == "SERVER"]
        assert len(queries) == 12
        assert len(servers) == 1
        assert {s.context.trace_id for s in queries} == {servers[0].context.trace_id}
        assert collector.payload["trace_id"] == f"{servers[0].context.trace_id:032x}"
        assert all(s.parent is not None for s in queries)
        [diagnostic] = [event for event in servers[0].events
                        if event.name == "fastapiobserver.db.n_plus_one"]
        assert diagnostic.attributes["count"] == 12
        assert "SELECT" not in str(diagnostic.attributes)
        # Connections opened on the TestClient loop must close there too.
        assert connections == {"opened": 2, "closed": 2}
    finally:
        uninstrument_sqlalchemy()
        for engine in engines:
            if asynchronous:
                asyncio.run(engine.dispose())
            else:
                engine.dispose()


def test_installed_distribution_includes_types_when_running_from_wheel():
    import fastapiobserver

    location = Path(fastapiobserver.__file__).parent
    assert (location / "py.typed").is_file()
    assert importlib.metadata.version("fastapi-observer") == fastapiobserver.__version__
