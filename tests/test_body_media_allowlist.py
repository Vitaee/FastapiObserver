from __future__ import annotations

import logging
from typing import Any

import pytest

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from fastapiobserver import ObservabilitySettings, SecurityPolicy, install_observability


@pytest.mark.parametrize(
    ("media_types", "content", "content_type", "captured"),
    [
        (("application/json",), b'{"hello": "world"}', "application/json", True),
        (("application/json",), b"\x00\x01\x02", "application/octet-stream", False),
        (None, b"\x00\x01\x02", "application/octet-stream", True),
    ],
)
def test_request_body_capture_follows_the_media_type_allowlist(
    caplog: Any, media_types: tuple[str, ...] | None, content: bytes, content_type: str,
    captured: bool,
) -> None:
    app = FastAPI()

    @app.post("/echo")
    async def echo(request: Request) -> dict[str, int]:
        return {"size": len(await request.body())}

    install_observability(
        app,
        ObservabilitySettings(app_name="test", service="svc", environment="test"),
        security_policy=SecurityPolicy(log_request_body=True, body_capture_media_types=media_types),
        metrics_enabled=False,
    )
    with caplog.at_level(logging.INFO, logger="fastapiobserver.middleware"):
        response = TestClient(app).post(
            "/echo", content=content, headers={"content-type": content_type}
        )

    assert response.status_code == 200
    [event] = [r.event for r in caplog.records if r.name == "fastapiobserver.middleware"]
    assert ("request_body" in event) is captured
    if captured and media_types:
        assert "hello" in event["request_body"]
