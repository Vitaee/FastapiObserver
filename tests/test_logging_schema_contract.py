from __future__ import annotations

import json
import logging
import time

from fastapiobserver import LOG_SCHEMA_VERSION, ObservabilitySettings, SecurityPolicy
from fastapiobserver import __version__ as package_version
from fastapiobserver.logging import StructuredJsonFormatter, setup_logging
from fastapiobserver.plugins import register_log_filter
from fastapiobserver.request_context import (
    clear_request_id,
    clear_user_context,
    set_request_id,
    set_user_context,
)


def test_structured_log_schema_contract() -> None:
    settings = ObservabilitySettings(
        app_name="orders-api",
        service="orders",
        environment="test",
        version="1.2.3",
    )
    formatter = StructuredJsonFormatter(settings, security_policy=SecurityPolicy())

    set_request_id("req-123")
    set_user_context({"user_id": "42", "password": "secret"})

    record = logging.LogRecord(
        name="tests.logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=20,
        msg="hello world",
        args=(),
        exc_info=None,
    )
    record.event = {"headers": {"authorization": "Bearer token"}}

    payload = json.loads(formatter.format(record))

    for required_key in (
        "timestamp",
        "level",
        "logger",
        "message",
        "app_name",
        "service",
        "environment",
        "version",
        "log_schema_version",
        "library",
        "library_version",
        "request_id",
    ):
        assert required_key in payload

    assert payload["log_schema_version"] == LOG_SCHEMA_VERSION
    assert payload["library"] == "fastapiobserver"
    assert payload["library_version"] == package_version
    assert payload["request_id"] == "req-123"
    assert payload["event"]["headers"]["authorization"] == "***"
    assert payload["user_context"]["password"] == "***"

    clear_request_id()
    clear_user_context()


def test_structured_formatter_supports_injected_dependencies() -> None:
    settings = ObservabilitySettings(
        app_name="orders-api",
        service="orders",
        environment="test",
        version="1.2.3",
    )
    calls: list[str] = []

    def custom_enricher(event: dict[str, object]) -> dict[str, object]:
        calls.append("enrich")
        enriched = dict(event)
        enriched["tenant"] = "acme"
        return enriched

    def custom_sanitizer(
        event: dict[str, object],
        _policy: SecurityPolicy,
    ) -> dict[str, object]:
        calls.append("sanitize")
        sanitized = dict(event)
        sanitized["sanitized_by"] = "custom"
        return sanitized

    formatter = StructuredJsonFormatter(
        settings,
        security_policy=SecurityPolicy(),
        enrich_event=custom_enricher,
        sanitize_payload=custom_sanitizer,
    )

    record = logging.LogRecord(
        name="tests.logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=20,
        msg="hello world",
        args=(),
        exc_info=None,
    )

    payload = json.loads(formatter.format(record))

    assert calls == ["enrich", "sanitize"]
    assert payload["tenant"] == "acme"
    assert payload["sanitized_by"] == "custom"


def test_setup_logging_is_idempotent_with_force_mode() -> None:
    settings = ObservabilitySettings(app_name="test", service="test", environment="test")
    root = logging.getLogger()

    setup_logging(settings, force=True)
    first_count = sum(
        1
        for handler in root.handlers
        if getattr(handler, "_fastapiobserver_managed", False)
    )

    setup_logging(settings, force=True)
    second_count = sum(
        1
        for handler in root.handlers
        if getattr(handler, "_fastapiobserver_managed", False)
    )

    assert first_count == 1
    assert second_count == 1


def test_setup_logging_applies_registered_log_filters() -> None:
    class _CollectingHandler(logging.Handler):
        def __init__(self) -> None:
            super().__init__()
            self.messages: list[str] = []

        def emit(self, record: logging.LogRecord) -> None:
            self.messages.append(self.format(record))

    register_log_filter(
        "drop.health",
        lambda record: "health probe" not in record.getMessage(),
    )

    settings = ObservabilitySettings(app_name="test", service="test", environment="test")
    collector = _CollectingHandler()
    setup_logging(
        settings,
        force=True,
        logs_mode="otlp",
        extra_handlers=[collector],
    )

    logger = logging.getLogger("tests.log_filter")
    logger.info("health probe")
    logger.info("business event")

    deadline = time.time() + 2.0
    while time.time() < deadline:
        payloads = [
            json.loads(message)
            for message in collector.messages
            if message.startswith("{")
        ]
        filtered = [p for p in payloads if p.get("logger") == "tests.log_filter"]
        if any(p.get("message") == "business event" for p in filtered):
            break
        time.sleep(0.01)

    payloads = [
        json.loads(message) for message in collector.messages if message.startswith("{")
    ]
    filtered = [p for p in payloads if p.get("logger") == "tests.log_filter"]

    assert any(p.get("message") == "business event" for p in filtered)
    assert not any(p.get("message") == "health probe" for p in filtered)


def test_structured_formatter_emits_structured_error_payload() -> None:
    settings = ObservabilitySettings(
        app_name="orders-api",
        service="orders",
        environment="test",
        version="1.2.3",
    )
    formatter = StructuredJsonFormatter(settings, security_policy=SecurityPolicy())

    try:
        raise RuntimeError("boom")
    except RuntimeError as error:
        exc_info = (error.__class__, error, error.__traceback__)
        record = logging.LogRecord(
            name="tests.logger",
            level=logging.ERROR,
            pathname=__file__,
            lineno=200,
            msg="request.failed",
            args=(),
            exc_info=exc_info,
        )

    payload = json.loads(formatter.format(record))

    assert payload["error"]["type"] == "RuntimeError"
    assert payload["error"]["message"] == "boom"
    assert "RuntimeError: boom" in payload["error"]["stacktrace"]
    assert payload["exc_info"] == payload["error"]["stacktrace"]
