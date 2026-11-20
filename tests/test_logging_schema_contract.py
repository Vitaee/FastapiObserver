from __future__ import annotations

import json
import logging
import logging.config
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from fastapiobserver import ObservabilitySettings, SecurityPolicy
from fastapiobserver.logging import formatter as formatter_module
from fastapiobserver.logging import LOG_SCHEMA_VERSION
from fastapiobserver import __version__ as package_version
from fastapiobserver.logging import StructuredJsonFormatter, flush_logging, shutdown_logging
from fastapiobserver.plugins import register_log_filter
from fastapiobserver.request_context import set_request_id, set_user_context
from tests.helpers import capture_logs


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


def test_queued_records_keep_exception_and_user_context() -> None:
    collector = capture_logs(
        ObservabilitySettings(app_name="test", service="test", environment="test")
    )

    set_user_context({"user_id": "42"})
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        logging.getLogger("tests.queued").exception("request failed")
    shutdown_logging()

    payload = next(p for p in collector.payloads if p["logger"] == "tests.queued")
    assert payload["message"] == "request failed"
    assert payload["error"]["type"] == "RuntimeError"
    assert payload["user_context"] == {"user_id": "42"}


def test_setup_logging_again_writes_each_line_once() -> None:
    settings = ObservabilitySettings(app_name="test", service="test", environment="test")
    capture_logs(settings)
    collector = capture_logs(settings)

    logging.getLogger("tests.twice").warning("once")
    assert flush_logging()

    assert [p["message"] for p in collector.payloads if p["logger"] == "tests.twice"] == ["once"]


def test_setup_logging_applies_registered_log_filters() -> None:
    register_log_filter(
        "drop.health",
        lambda record: "health probe" not in record.getMessage(),
    )
    collector = capture_logs(
        ObservabilitySettings(app_name="test", service="test", environment="test")
    )

    logger = logging.getLogger("tests.log_filter")
    logger.info("health probe")
    logger.info("business event")
    assert flush_logging()

    messages = [p["message"] for p in collector.payloads if p["logger"] == "tests.log_filter"]
    assert messages == ["business event"]


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
    assert "fingerprint" in payload["error"]
    # The stack trace is written once, in `error`.
    assert "exc_info" not in payload


def test_error_fingerprint_groups_by_where_it_was_raised_not_by_message() -> None:
    formatter = StructuredJsonFormatter(
        ObservabilitySettings(app_name="test", service="test", environment="test")
    )

    def fingerprint(user_id: int, other_site: bool = False) -> str:
        try:
            if other_site:
                raise LookupError(f"user {user_id} not found")
            raise LookupError(f"user {user_id} not found at 0x{id(object()):x}")
        except LookupError:
            record = logging.LogRecord(
                name="test", level=logging.ERROR, pathname="", lineno=0,
                msg="error", args=(), exc_info=sys.exc_info(),
            )
        return str(json.loads(formatter.format(record))["error"]["fingerprint"])

    assert fingerprint(1) == fingerprint(2)
    assert fingerprint(1) != fingerprint(1, other_site=True)
    assert len(fingerprint(1)) == 32

def test_request_id_filter_standalone() -> None:
    from fastapiobserver.logging import RequestIdFilter
    f = RequestIdFilter()
    record = logging.LogRecord("test", logging.INFO, "", 0, "msg", (), None)
    set_request_id("filter-req-1")
    f.filter(record)
    assert getattr(record, "request_id", None) == "filter-req-1"

def test_trace_context_filter_standalone() -> None:
    from fastapiobserver.logging import TraceContextFilter
    f = TraceContextFilter()
    record = logging.LogRecord("test", logging.INFO, "", 0, "msg", (), None)

    assert f.filter(record)
    assert not hasattr(record, "trace_id")


def test_formatter_built_by_dictconfig_reads_settings_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Server log configs (gunicorn logconfig_dict, Granian --log-config) build the
    # formatter by name, without arguments.
    monkeypatch.setenv("OBS_APP_NAME", "orders-api")
    monkeypatch.setenv("OBS_SERVICE_NAME", "orders")
    logging.config.dictConfig({
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"json": {"()": "fastapiobserver.StructuredJsonFormatter"}},
        "handlers": {"server": {"class": "logging.NullHandler", "formatter": "json"}},
        "loggers": {"tests.server": {"handlers": ["server"], "propagate": False}},
    })
    server_logger = logging.getLogger("tests.server")
    try:
        formatter = server_logger.handlers[0].formatter
        assert formatter is not None
        record = server_logger.makeRecord(
            "tests.server", logging.INFO, __file__, 1, "Listening", (), None
        )
        payload = json.loads(formatter.format(record))
    finally:
        server_logger.handlers.clear()
    assert payload["app_name"] == "orders-api"
    assert payload["service"] == "orders"
    assert payload["message"] == "Listening"


def _logged_event_value(value: object) -> object:
    formatter = StructuredJsonFormatter(ObservabilitySettings(), security_policy=SecurityPolicy())
    record = logging.LogRecord("tests.logger", logging.INFO, __file__, 1, "order.paid", (), None)
    record.event = {"value": value}
    return json.loads(formatter.format(record))["event"]["value"]


@pytest.mark.parametrize(
    "value",
    [Decimal("19.99"), {"a"}, b"abc", Path("/tmp/x"), 2**70],
    ids=["decimal", "set", "bytes", "path", "big-int"],
)
def test_orjson_writes_the_values_the_standard_library_writes(
    value: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    # orjson alone rejects these, which lost the line and counted as a sink failure.
    pytest.importorskip("orjson")
    with_orjson = _logged_event_value(value)

    monkeypatch.setattr(formatter_module, "orjson", None)
    assert with_orjson == _logged_event_value(value)


def test_a_record_the_fast_encoder_rejects_is_still_written(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Runs without orjson installed: any encoder error falls back to the standard library.
    def reject(payload: object, default: object = None) -> bytes:
        raise TypeError("Integer exceeds 64-bit range")

    monkeypatch.setattr(formatter_module, "orjson", SimpleNamespace(dumps=reject))

    assert _logged_event_value(2**70) == 2**70
