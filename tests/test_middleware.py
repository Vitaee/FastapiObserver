from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient

from observabilityfastapi import ObservabilitySettings, install_observability
from observabilityfastapi.request_context import get_request_id


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
    with caplog.at_level(logging.INFO, logger="observabilityfastapi.middleware"):
        response = client.get("/search?token=secret")

    assert response.status_code == 200
    request_logs = [
        record
        for record in caplog.records
        if record.name == "observabilityfastapi.middleware" and hasattr(record, "event")
    ]
    assert request_logs
    assert request_logs[-1].event["path"] == "/search"
