from __future__ import annotations

import logging

from fastapi import FastAPI
import pytest

import fastapiobserver.fastapi as fastapi_module
import fastapiobserver.otel as otel_module
from fastapiobserver.fastapi import install_observability
from fastapiobserver.otel import OTelLogsSettings
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
    handler = otel_module._SanitizingOTLPLogHandler(  # type: ignore[attr-defined]
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
    assert record.event["password"] == "secret"
