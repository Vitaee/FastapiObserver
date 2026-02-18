from __future__ import annotations

import json
import logging

from fastapiobserver import LOG_SCHEMA_VERSION, ObservabilitySettings, SecurityPolicy
from fastapiobserver import __version__ as package_version
from fastapiobserver.logging import StructuredJsonFormatter, setup_logging
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
