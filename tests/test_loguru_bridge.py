from __future__ import annotations

import logging

import pytest

from fastapiobserver.loguru import install_loguru_bridge, remove_loguru_bridge


class _CaptureHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _prepare_logger(name: str) -> tuple[logging.Logger, _CaptureHandler]:
    logger = logging.getLogger(name)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    capture = _CaptureHandler()
    logger.addHandler(capture)
    return logger, capture


def test_loguru_bridge_forwards_message_and_extra_fields() -> None:
    loguru = pytest.importorskip("loguru")
    logger_name = "tests.loguru.bridge"
    logger, capture = _prepare_logger(logger_name)
    bridge_id = install_loguru_bridge(
        stdlib_logger_name=logger_name,
        include_extra=True,
    )

    try:
        loguru.logger.bind(
            request_id="req-123",
            event={"kind": "bridge"},
        ).info("hello from loguru")
    finally:
        remove_loguru_bridge(bridge_id)
        logger.removeHandler(capture)

    forwarded = [record for record in capture.records if record.getMessage() == "hello from loguru"]
    assert forwarded
    record = forwarded[-1]
    assert getattr(record, "request_id", None) == "req-123"
    assert getattr(record, "event", None) == {"kind": "bridge"}


def test_loguru_bridge_forwards_exception_info() -> None:
    loguru = pytest.importorskip("loguru")
    logger_name = "tests.loguru.exception"
    logger, capture = _prepare_logger(logger_name)
    bridge_id = install_loguru_bridge(stdlib_logger_name=logger_name)

    try:
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            loguru.logger.exception("loguru failed")
    finally:
        remove_loguru_bridge(bridge_id)
        logger.removeHandler(capture)

    forwarded = [record for record in capture.records if record.getMessage() == "loguru failed"]
    assert forwarded
    record = forwarded[-1]
    assert record.exc_info is not None
    assert record.exc_info[0] is RuntimeError
