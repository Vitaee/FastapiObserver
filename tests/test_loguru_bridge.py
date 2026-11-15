from __future__ import annotations

import logging

import pytest

from fastapiobserver.loguru import install_loguru_bridge, remove_loguru_bridge


def _forwarded(caplog: pytest.LogCaptureFixture, name: str, message: str) -> logging.LogRecord:
    [record] = [r for r in caplog.records if r.name == name and r.getMessage() == message]
    return record


def test_loguru_bridge_forwards_message_and_extra_fields(
    caplog: pytest.LogCaptureFixture,
) -> None:
    loguru = pytest.importorskip("loguru")
    logger_name = "tests.loguru.bridge"
    bridge_id = install_loguru_bridge(stdlib_logger_name=logger_name, include_extra=True)

    try:
        with caplog.at_level(logging.DEBUG, logger=logger_name):
            loguru.logger.bind(
                request_id="req-123",
                event={"kind": "bridge"},
            ).info("hello from loguru")
    finally:
        remove_loguru_bridge(bridge_id)

    record = _forwarded(caplog, logger_name, "hello from loguru")
    assert getattr(record, "request_id", None) == "req-123"
    assert getattr(record, "event", None) == {"kind": "bridge"}


def test_loguru_bridge_forwards_exception_info(caplog: pytest.LogCaptureFixture) -> None:
    loguru = pytest.importorskip("loguru")
    logger_name = "tests.loguru.exception"
    bridge_id = install_loguru_bridge(stdlib_logger_name=logger_name)

    try:
        with caplog.at_level(logging.DEBUG, logger=logger_name):
            try:
                raise RuntimeError("boom")
            except RuntimeError:
                loguru.logger.exception("loguru failed")
    finally:
        remove_loguru_bridge(bridge_id)

    record = _forwarded(caplog, logger_name, "loguru failed")
    assert record.exc_info is not None
    assert record.exc_info[0] is RuntimeError
