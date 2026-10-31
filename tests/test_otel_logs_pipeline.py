from __future__ import annotations

import asyncio
import logging
import warnings
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
import pytest

import fastapiobserver.fastapi as fastapi_module
from fastapiobserver.fastapi import install_observability
from fastapiobserver.otel import OTelLogsSettings
from fastapiobserver.otel.logs import _SanitizingOTLPLogHandler
from fastapiobserver.security import SecurityPolicy
from fastapiobserver.config import ObservabilitySettings


class _CollectingHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def test_otlp_mode_requires_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    app = FastAPI()
    settings = ObservabilitySettings()

    monkeypatch.setattr(fastapi_module, "install_otel_logs", lambda *args, **kwargs: None)

    with pytest.raises(RuntimeError, match="OTLP log mode requires"):
        install_observability(
            app,
            settings,
            otel_logs_settings=OTelLogsSettings(enabled=True, logs_mode="otlp"),
        )


def test_sanitizing_otlp_handler_sanitizes_custom_attributes() -> None:
    collector = _CollectingHandler()
    policy = SecurityPolicy()
    handler = _SanitizingOTLPLogHandler(
        collector,
        security_policy=policy,
    )

    record = logging.makeLogRecord(
        {
            "name": "demo",
            "levelname": "INFO",
            "levelno": logging.INFO,
            "msg": "hello",
            "password": "secret",
            "event": {
                "password": "secret",
                "headers": {"authorization": "Bearer abc"},
            },
        }
    )

    handler.emit(record)

    assert collector.records
    emitted = collector.records[0]
    assert getattr(emitted, "password") == "***"
    event = getattr(emitted, "event")
    assert event["password"] == "***"
    assert event["headers"]["authorization"] == "***"

    # Wrapper emits a cloned record so sibling handlers see the original.
    assert getattr(record, "password") == "secret"
    record_event = getattr(record, "event")
    assert record_event["password"] == "secret"


def _install_otlp_logs_app(monkeypatch: pytest.MonkeyPatch, app: FastAPI) -> Any:
    in_memory = pytest.importorskip("opentelemetry.sdk._logs.export")
    import fastapiobserver.otel.logs as otel_logs_module

    exporter = in_memory.InMemoryLogRecordExporter()
    monkeypatch.setattr(otel_logs_module, "build_otlp_exporter", lambda *_: exporter)
    install_observability(
        app,
        ObservabilitySettings(app_name="test", service="svc", environment="test"),
        metrics_enabled=False,
        otel_logs_settings=OTelLogsSettings(enabled=True, logs_mode="otlp"),
    )
    return exporter


def _run_lifespan(app: FastAPI) -> None:
    async def _cycle() -> None:
        async with app.router.lifespan_context(app):
            pass

    asyncio.run(_cycle())


def test_otlp_logs_are_redacted_without_deprecated_or_root_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        exporter = _install_otlp_logs_app(monkeypatch, app)

    assert not [w for w in caught if issubclass(w.category, DeprecationWarning)]
    # Only our queue handler may sit on root; an OTel root handler would skip redaction.
    assert not any(
        type(handler).__module__.startswith("opentelemetry")
        for handler in logging.getLogger().handlers
    )

    logging.getLogger("demo").warning("login", extra={"password": "secret"})
    _run_lifespan(app)

    [exported] = [
        item
        for item in exporter.get_finished_logs()
        if item.log_record.attributes.get("password")
    ]
    assert exported.log_record.attributes["password"] == "***"


def test_logs_from_app_shutdown_reach_otlp_before_provider_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
        yield
        logging.getLogger("demo").warning("app.stopping")

    app = FastAPI(lifespan=lifespan)
    exporter = _install_otlp_logs_app(monkeypatch, app)

    _run_lifespan(app)

    bodies = [str(item.log_record.body) for item in exporter.get_finished_logs()]
    assert any("app.stopping" in body for body in bodies)
