from __future__ import annotations

import logging

from fastapi import FastAPI, Response
from fastapi.testclient import TestClient
import pytest

from fastapiobserver import ObservabilitySettings, install_observability
from fastapiobserver.request_context import get_request_id


def test_middleware_sets_response_request_id_header() -> None:
    app = FastAPI()

    @app.get("/id")
    def read_id() -> dict[str, str | None]:
        return {"request_id": get_request_id()}

    install_observability(
        app,
        ObservabilitySettings(app_name="test", service="svc", environment="test"),
        metrics_enabled=False,
    )

    client = TestClient(app)
    response = client.get("/id")

    assert response.status_code == 200
    assert "x-request-id" in response.headers
    assert response.headers["x-request-id"] == response.json()["request_id"]


def test_middleware_logs_path_without_query_string(caplog) -> None:
    app = FastAPI()

    @app.get("/search")
    def search() -> dict[str, bool]:
        return {"ok": True}

    install_observability(
        app,
        ObservabilitySettings(app_name="test", service="svc", environment="test"),
        metrics_enabled=False,
    )

    client = TestClient(app)
    with caplog.at_level(logging.INFO, logger="fastapiobserver.middleware"):
        response = client.get("/search?token=secret")

    assert response.status_code == 200
    request_logs = [
        record
        for record in caplog.records
        if record.name == "fastapiobserver.middleware" and hasattr(record, "event")
    ]
    assert request_logs
    assert request_logs[-1].event["path"] == "/search"
    assert request_logs[-1].event["url.path"] == "/search"
    assert request_logs[-1].event["http.request.method"] == "GET"
    assert request_logs[-1].event["http.response.status_code"] == 200


def test_middleware_classifies_server_error_response(caplog) -> None:
    app = FastAPI()

    @app.get("/down")
    def down() -> Response:
        return Response(status_code=503)

    install_observability(
        app,
        ObservabilitySettings(app_name="test", service="svc", environment="test"),
        metrics_enabled=False,
    )

    with caplog.at_level(logging.WARNING, logger="fastapiobserver.middleware"):
        response = TestClient(app).get("/down")

    assert response.status_code == 503
    request_logs = [
        record
        for record in caplog.records
        if record.name == "fastapiobserver.middleware" and hasattr(record, "event")
    ]
    assert request_logs
    assert request_logs[-1].event["error_type"] == "server_error"
    assert request_logs[-1].event["http.request.method"] == "GET"
    assert request_logs[-1].event["url.path"] == "/down"
    assert request_logs[-1].event["http.response.status_code"] == 503


def test_middleware_classifies_unhandled_exception(caplog) -> None:
    app = FastAPI()

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("boom")

    install_observability(
        app,
        ObservabilitySettings(app_name="test", service="svc", environment="test"),
        metrics_enabled=False,
    )

    client = TestClient(app, raise_server_exceptions=False)
    with caplog.at_level(logging.ERROR, logger="fastapiobserver.middleware"):
        response = client.get("/boom")

    assert response.status_code == 500
    request_logs = [
        record
        for record in caplog.records
        if record.name == "fastapiobserver.middleware" and hasattr(record, "event")
    ]
    assert request_logs
    assert request_logs[-1].event["error_type"] == "unhandled_exception"
    assert request_logs[-1].event["exception_class"] == "RuntimeError"
    assert request_logs[-1].event["exception_message"] == "boom"
    assert request_logs[-1].event["http.request.method"] == "GET"
    assert request_logs[-1].event["url.path"] == "/boom"
    assert request_logs[-1].event["http.response.status_code"] == 500


def test_middleware_records_exception_on_active_span(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    otel_trace = pytest.importorskip("opentelemetry.trace")

    class _SpanContext:
        is_valid = True

    class _Span:
        def __init__(self) -> None:
            self.exceptions: list[Exception] = []
            self.status: object | None = None

        def get_span_context(self) -> _SpanContext:
            return _SpanContext()

        def record_exception(self, error: Exception) -> None:
            self.exceptions.append(error)

        def set_status(self, status: object) -> None:
            self.status = status

    span = _Span()
    monkeypatch.setattr(otel_trace, "get_current_span", lambda: span)

    app = FastAPI()

    @app.get("/boom-span")
    def boom() -> None:
        raise RuntimeError("boom")

    install_observability(
        app,
        ObservabilitySettings(app_name="test", service="svc", environment="test"),
        metrics_enabled=False,
    )

    response = TestClient(app, raise_server_exceptions=False).get("/boom-span")
    assert response.status_code == 500
    assert span.exceptions
    assert isinstance(span.exceptions[0], RuntimeError)
    assert span.status is not None

def test_extract_scope_client_ip_contract() -> None:
    from fastapiobserver.middleware import _extract_scope_client_ip

    assert _extract_scope_client_ip({"client": ("192.168.1.1", 1234)}) == "192.168.1.1"
    assert _extract_scope_client_ip({"client": None}) is None
    assert _extract_scope_client_ip({}) is None

def test_resolve_request_id_contract() -> None:
    from fastapiobserver.middleware import _resolve_request_id
    import uuid

    # Generated if false
    req_id = _resolve_request_id(None, False)
    assert isinstance(uuid.UUID(req_id), uuid.UUID)

    # Trusted candidate returns itself
    req_id = _resolve_request_id("custom-id", True)
    assert req_id == "custom-id"

    # Untrusted candidate generates
    req_id = _resolve_request_id("custom-id", False)
    assert isinstance(uuid.UUID(req_id), uuid.UUID)

def test_extract_route_template_contract() -> None:
    from fastapiobserver.middleware import _extract_route_template

    assert _extract_route_template({}, "/raw") == "/raw"

    scope = {
        "endpoint": lambda: None,
        "route": type("Route", (), {"path": "/users/{user_id}"})(),
    }
    assert _extract_route_template(scope, "/raw") == "/users/{user_id}"
