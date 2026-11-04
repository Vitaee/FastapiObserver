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
    from sqlalchemy import create_engine, text
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    import fastapiobserver.otel.tracing as tracing

    exporter = InMemorySpanExporter()
    monkeypatch.setattr(tracing, "build_otlp_exporter", lambda *_: exporter)
    uninstrument_sqlalchemy()
    engines = [create_async_engine("sqlite+aiosqlite://", poolclass=NullPool)
               if asynchronous else create_engine("sqlite://") for _ in range(2)]
    app = FastAPI()

    @app.get("/queries")
    async def queries():
        for engine in engines:
            if asynchronous:
                async with engine.connect() as connection:
                    for number in range(3):
                        await connection.execute(text("SELECT :number"), {"number": number})
            else:
                with engine.connect() as connection:
                    for number in range(3):
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
        assert collector.payload["event"]["db"]["count"] == 6
        spans = exporter.get_finished_spans()
        queries = [s for s in spans if s.attributes.get("db.statement", "").startswith("SELECT")]
        servers = [s for s in spans if s.kind.name == "SERVER"]
        assert len(queries) == 6
        assert len(servers) == 1
        assert {s.context.trace_id for s in queries} == {servers[0].context.trace_id}
        assert collector.payload["trace_id"] == f"{servers[0].context.trace_id:032x}"
        assert all(s.parent is not None for s in queries)
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
