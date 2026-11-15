"""Verify application-owned JSON logging through a real Uvicorn server."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx2
import pytest

from tests.helpers import subprocess_env, uvicorn_server, wait_for

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def logging_server(tmp_path):
    env = subprocess_env(
        OBS_OTEL_ENABLED="false",
        # Even an environment enabling log export must not add a
        # second pipeline beside the application's approved handler.
        OBS_OTEL_LOGS_ENABLED="true",
        OBS_OTEL_LOGS_MODE="both",
        OBS_OTEL_METRICS_ENABLED="false",
        OBS_RUNTIME_CONTROL_ENABLED="false",
    )
    log_path = tmp_path / "application.log"
    with uvicorn_server("examples.existing_logging_app:app", log_path, cwd=ROOT, env=env) as (
        _, base_url,
    ), httpx2.Client(base_url=base_url, timeout=1) as client:
        yield client, log_path


def _diagnostics_complete(rows: list[dict]) -> list[dict] | None:
    diagnostics = [p for p in rows if p["message"].startswith("request.")]
    orders = [p for p in diagnostics if p["event"]["path"].startswith("/orders/")]
    failed = [p for p in diagnostics if p["message"] == "request.failed" and p.get("error")]
    return rows if len(orders) == 12 and failed else None


def test_existing_logger_redacts_failures_and_isolates_concurrent_requests(logging_server):
    client, log_path = logging_server
    with ThreadPoolExecutor(max_workers=6) as executor:
        responses = list(executor.map(lambda n: client.get(f"/orders/{n}"), range(12)))
    failure = client.get("/failure", headers={"x-request-id": "example-failure"})
    assert failure.status_code == 500
    assert all(r.status_code == 200 for r in responses)
    request_ids = {r.headers["x-request-id"] for r in responses}
    assert len(request_ids) == 12

    # The response reaches the client before its completion line is written.
    payloads = wait_for(log_path, _diagnostics_complete)
    diagnostics = [p for p in payloads if p["message"].startswith("request.")]
    failures = [p for p in diagnostics if p["message"] == "request.failed"]
    text = log_path.read_text()

    for response in responses:
        request_id = response.headers["x-request-id"]
        [diagnostic] = [p for p in diagnostics if p.get("request_id") == request_id]
        assert diagnostic["event"]["path"] == response.request.url.path
        assert diagnostic["event"]["status_code"] == 200
        assert diagnostic["event"]["response_completed"] is True
        assert diagnostic["user_context"]["tenant_slug"] == "example"
        [application_event] = [
            p for p in payloads
            if p["message"] == "order.accepted demo=[redacted] token=[redacted]"
            and p.get("request_id") == request_id
        ]
        assert application_event["event"]["order_id"] == response.json()["order_id"]

    [failed] = failures
    assert failed["request_id"] == "example-failure"
    assert "RuntimeError" in failed["error"]
    assert "token=[redacted]" in failed["error"]
    assert "example-secret" not in text
    assert "demo@example.test" not in text
    # Uvicorn's access destination is preserved; suppression is the app's choice.
    assert any(p["logger"] == "uvicorn.access" for p in payloads)
