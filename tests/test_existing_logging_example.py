"""Verify application-owned JSON logging through a real Uvicorn server."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def logging_server(tmp_path):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"
    log_path = tmp_path / "application.log"
    with log_path.open("w") as output:
        process = subprocess.Popen(
            [
                sys.executable, "-m", "uvicorn", "examples.existing_logging_app:app",
                "--host", "127.0.0.1", "--port", str(port),
            ],
            cwd=ROOT,
            env={
                **os.environ,
                "PYTHONPATH": str(ROOT / "src"),
                "PYTHONUNBUFFERED": "1",
                "OBS_OTEL_ENABLED": "false",
                # Even an environment enabling log export must not add a
                # second pipeline beside the application's approved handler.
                "OBS_OTEL_LOGS_ENABLED": "true",
                "OBS_OTEL_LOGS_MODE": "both",
                "OBS_OTEL_METRICS_ENABLED": "false",
                "OBS_RUNTIME_CONTROL_ENABLED": "false",
            },
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        try:
            with httpx.Client(base_url=base_url, timeout=1) as client:
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    assert process.poll() is None, log_path.read_text()
                    try:
                        if client.get("/health").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.02)
                else:
                    pytest.fail(f"Example did not become ready:\n{log_path.read_text()}")
                yield client, log_path
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


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
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        text = log_path.read_text()
        payloads = [json.loads(line) for line in text.splitlines() if line.strip()]
        diagnostics = [p for p in payloads if p["message"].startswith("request.")]
        if len([p for p in diagnostics if p["event"]["path"].startswith("/orders/")]) == 12:
            failures = [p for p in diagnostics if p["message"] == "request.failed"]
            if failures and any(p.get("error") for p in failures):
                break
        time.sleep(0.02)
    else:
        pytest.fail(f"Expected diagnostics missing:\n{text}")

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
