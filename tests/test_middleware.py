from __future__ import annotations

import logging

from fastapi import FastAPI, Response
from fastapi.testclient import TestClient

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
