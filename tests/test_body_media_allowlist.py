from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from fastapiobserver import ObservabilitySettings, SecurityPolicy, install_observability


def _build_app(policy: SecurityPolicy) -> FastAPI:
    app = FastAPI()

    @app.post("/echo")
    async def echo(request: Request) -> dict[str, int]:
        payload = await request.body()
        return {"size": len(payload)}

    install_observability(
        app,
        ObservabilitySettings(app_name="test", service="svc", environment="test"),
        security_policy=policy,
        metrics_enabled=False,
    )
    return app


def _latest_event(caplog: Any) -> dict[str, Any]:
    for record in reversed(caplog.records):
        if record.name == "fastapiobserver.middleware" and hasattr(record, "event"):
            return record.event
    raise AssertionError("No middleware log event found")


def test_json_body_captured_when_in_allowlist(caplog: Any) -> None:
    app = _build_app(
        SecurityPolicy(
            log_request_body=True,
            body_capture_media_types=("application/json",),
        )
    )

    with caplog.at_level(logging.INFO, logger="fastapiobserver.middleware"):
        response = TestClient(app).post("/echo", json={"hello": "world"})

    assert response.status_code == 200
    event = _latest_event(caplog)
    assert "request_body" in event
    assert "hello" in event["request_body"]


def test_binary_body_not_captured_when_not_in_allowlist(caplog: Any) -> None:
    app = _build_app(
        SecurityPolicy(
            log_request_body=True,
            body_capture_media_types=("application/json",),
        )
    )

    with caplog.at_level(logging.INFO, logger="fastapiobserver.middleware"):
        response = TestClient(app).post(
            "/echo",
            content=b"\x00\x01\x02",
            headers={"content-type": "application/octet-stream"},
        )

    assert response.status_code == 200
    event = _latest_event(caplog)
    assert "request_body" not in event


def test_all_bodies_captured_when_allowlist_is_none(caplog: Any) -> None:
    app = _build_app(SecurityPolicy(log_request_body=True))

    with caplog.at_level(logging.INFO, logger="fastapiobserver.middleware"):
        response = TestClient(app).post(
            "/echo",
            content=b"\x00\x01\x02",
            headers={"content-type": "application/octet-stream"},
        )

    assert response.status_code == 200
    event = _latest_event(caplog)
    assert "request_body" in event
