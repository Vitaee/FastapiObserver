from __future__ import annotations

import logging

from starlette.requests import Request
from starlette.responses import Response

from fastapiobserver.plugins import (
    apply_log_filters,
    apply_log_enrichers,
    emit_metric_hooks,
    register_log_enricher,
    register_log_filter,
    register_metric_hook,
)


def _request() -> Request:
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/health",
        "raw_path": b"/health",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }
    return Request(scope)


def test_log_enricher_failures_are_isolated() -> None:
    def bad_enricher(event: dict[str, object]) -> dict[str, object]:
        raise RuntimeError("boom")

    def good_enricher(event: dict[str, object]) -> dict[str, object]:
        event["tenant"] = "acme"
        return event

    register_log_enricher("bad", bad_enricher)
    register_log_enricher("good", good_enricher)

    payload = apply_log_enrichers({"message": "ok"})

    assert payload["message"] == "ok"
    assert payload["tenant"] == "acme"


def test_metric_hook_failures_are_isolated() -> None:
    called: list[str] = []

    def bad_hook(request: Request, response: Response, duration: float) -> None:
        raise RuntimeError("boom")

    def good_hook(request: Request, response: Response, duration: float) -> None:
        called.append(f"{request.method}:{response.status_code}:{duration}")

    register_metric_hook("bad", bad_hook)
    register_metric_hook("good", good_hook)

    emit_metric_hooks(_request(), Response(status_code=200), 0.12)

    assert called


def test_log_filters_can_drop_records() -> None:
    register_log_filter("drop_debug", lambda record: record.levelno >= logging.INFO)

    debug_record = logging.makeLogRecord({"levelno": logging.DEBUG, "msg": "ignore"})
    info_record = logging.makeLogRecord({"levelno": logging.INFO, "msg": "keep"})

    assert apply_log_filters(debug_record) is False
    assert apply_log_filters(info_record) is True


def test_log_filter_failures_are_isolated() -> None:
    def bad_filter(_record: logging.LogRecord) -> bool:
        raise RuntimeError("boom")

    register_log_filter("bad", bad_filter)

    record = logging.makeLogRecord({"levelno": logging.INFO, "msg": "ok"})
    assert apply_log_filters(record) is True
