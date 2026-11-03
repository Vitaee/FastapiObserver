"""Exercise the documented demo through a real server and real HTTP requests."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx2
import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def demo_server(tmp_path_factory: pytest.TempPathFactory):
    class Downstream(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"request_id": self.headers.get("x-request-id")}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    downstream = ThreadingHTTPServer(("127.0.0.1", 0), Downstream)
    thread = threading.Thread(target=downstream.serve_forever, daemon=True)
    thread.start()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"
    target = f"http://127.0.0.1:{downstream.server_port}"
    log_path = tmp_path_factory.mktemp("full-stack") / "server.log"
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT / "src"),
        "PYTHONUNBUFFERED": "1",
        "OBS_SERVICE_NAME": "example-test",
        "OBS_METRICS_ENABLED": "true",
        "OBS_LOOP_BLOCK_THRESHOLD_MS": "100",
        "OBS_OTEL_ENABLED": "false",
        "OBS_OTEL_LOGS_ENABLED": "false",
        "OBS_OTEL_METRICS_ENABLED": "false",
        "OBS_RUNTIME_CONTROL_ENABLED": "false",
        "OTEL_SDK_DISABLED": "true",
        "TARGET_B": target,
        "TARGET_C": target,
    }
    with log_path.open("w") as output:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "examples.full_stack.app:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--no-access-log",
            ],
            cwd=ROOT,
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                assert process.poll() is None, log_path.read_text()
                try:
                    if httpx2.get(f"{base_url}/health", timeout=0.3).status_code == 200:
                        break
                except httpx2.HTTPError:
                    pass
                time.sleep(0.02)
            else:
                pytest.fail(f"Demo did not become ready:\n{log_path.read_text()}")
            yield base_url, log_path
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            downstream.shutdown()
            downstream.server_close()
            thread.join(timeout=2)


def _records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.startswith("{")]


def test_demo_forwards_request_ids_to_both_downstream_services(demo_server) -> None:
    base_url, _ = demo_server

    response = httpx2.get(f"{base_url}/chain", headers={"x-request-id": "example-chain"})

    assert response.status_code == 200
    assert response.json()["results"] == {
        "app_b": {"request_id": "example-chain"},
        "app_c": {"request_id": "example-chain"},
    }


def test_failure_lab_produces_the_named_warnings(demo_server) -> None:
    base_url, log_path = demo_server
    completed = subprocess.run(
        [sys.executable, "examples/full_stack/failure_lab.py", "--base-url", base_url],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert completed.returncode == 0, completed.stderr
    expected = {"event_loop.blocked", "db.pool_exhausted", "db.n_plus_one"}
    assert all(name in completed.stdout for name in expected)
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        messages = {row["message"] for row in _records(log_path)}
        if expected <= messages:
            break
        time.sleep(0.02)
    assert expected <= messages


def test_batched_query_returns_the_same_data_with_one_query(demo_server) -> None:
    base_url, log_path = demo_server
    with httpx2.Client(base_url=base_url) as client:
        slow = client.get("/lab/n-plus-one")
        fast = client.get("/lab/batched")
    assert slow.status_code == fast.status_code == 200
    assert slow.json() == fast.json()
    assert len(fast.json()) == 10
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        counts = {
            row["event"]["path"]: row["event"]["db"]["count"]
            for row in _records(log_path)
            if row["message"] == "request.completed" and "db" in row.get("event", {})
        }
        if "/lab/batched" in counts:
            break
        time.sleep(0.02)
    assert counts["/lab/n-plus-one"] == 11
    assert counts["/lab/batched"] == 1


@pytest.mark.parametrize("path", ["/lab/block?seconds=20", "/lab/db-pool?seconds=-1"])
def test_failure_durations_are_bounded(demo_server, path: str) -> None:
    base_url, _ = demo_server
    assert httpx2.get(base_url + path).status_code == 422


@pytest.mark.parametrize("status", [400, 404, 500, 503])
def test_error_route_returns_the_requested_status(demo_server, status: int) -> None:
    base_url, _ = demo_server
    response = httpx2.get(f"{base_url}/error?status={status}")
    assert response.status_code == status
    assert response.json() == {"detail": "Intentional demo error"}


def test_traffic_generator_rejects_invalid_duration() -> None:
    result = subprocess.run(
        ["bash", "examples/full_stack/generate_traffic.sh", "invalid"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=2,
    )
    assert result.returncode != 0
    assert "positive integer" in result.stderr


def test_traffic_generator_stops_waiting_for_an_unavailable_service() -> None:
    with socket.socket() as unavailable:
        unavailable.bind(("127.0.0.1", 0))
        port = unavailable.getsockname()[1]
        result = subprocess.run(
            ["bash", "examples/full_stack/generate_traffic.sh", "1"],
            cwd=ROOT,
            env={**os.environ, "APPS": f"http://127.0.0.1:{port}", "READY_TIMEOUT": "1"},
            capture_output=True,
            text=True,
            timeout=5,
        )
    assert result.returncode == 1
    assert "not ready within 1s" in result.stderr
