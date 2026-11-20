"""Exercise the documented demo through a real server and real HTTP requests."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest

from tests.helpers import records, subprocess_env, uvicorn_server, wait_for, wait_record

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def demo_server(tmp_path_factory: pytest.TempPathFactory):
    class Downstream(BaseHTTPRequestHandler):
        def do_GET(self):
            parts = urlsplit(self.path)
            seconds = float(parse_qs(parts.query).get("seconds", ["0"])[0])
            if parts.path == "/lab/stream":
                self.send_response(200)
                self.send_header("Content-Length", "11")
                self.end_headers()
                self.wfile.write(b"first\n")
                self.wfile.flush()
                time.sleep(seconds)
                try:
                    self.wfile.write(b"last\n")
                except (BrokenPipeError, ConnectionResetError):
                    pass
                return
            time.sleep(seconds)
            body = json.dumps({"request_id": self.headers.get("x-request-id")}).encode()
            self.send_response(503 if parts.path == "/error" else 200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *_args):
            pass

    class DownstreamServer(ThreadingHTTPServer):
        # The concurrency test opens twelve connections together; avoid testing
        # the stdlib server's five-connection accept backlog instead of the app.
        request_queue_size = 64

    downstream = DownstreamServer(("127.0.0.1", 0), Downstream)
    thread = threading.Thread(target=downstream.serve_forever, daemon=True)
    thread.start()
    target = f"http://127.0.0.1:{downstream.server_port}"
    env = subprocess_env(
        OBS_SERVICE_NAME="example-test",
        OBS_METRICS_ENABLED="true",
        OBS_LOOP_BLOCK_THRESHOLD_MS="100",
        OBS_OTEL_ENABLED="false",
        OBS_OTEL_LOGS_ENABLED="false",
        OBS_OTEL_METRICS_ENABLED="false",
        OBS_RUNTIME_CONTROL_ENABLED="false",
        OTEL_SDK_DISABLED="true",
        TARGET_B=target,
        TARGET_C=target,
    )
    log_path = tmp_path_factory.mktemp("full-stack") / "server.log"
    try:
        with uvicorn_server(
            "examples.full_stack.app:app", log_path, cwd=ROOT, env=env, args=("--no-access-log",)
        ) as (_, base_url):
            yield base_url, log_path
    finally:
        downstream.shutdown()
        downstream.server_close()
        thread.join(timeout=2)


def test_log_reader_waits_for_a_complete_line(tmp_path: Path) -> None:
    path = tmp_path / "server.log"
    path.write_text('{"message":"first"}\n{"message":"sec')
    assert records(path) == [{"message": "first"}]

    with path.open("a") as output:
        output.write('ond"}\n')
    assert records(path) == [{"message": "first"}, {"message": "second"}]

    # A complete, malformed record is a real failure and must still be rejected.
    path.write_text('{"message":}\n')
    with pytest.raises(json.JSONDecodeError):
        records(path)


def test_demo_forwards_request_ids_to_both_downstream_services(demo_server) -> None:
    base_url, _ = demo_server

    response = httpx2.get(f"{base_url}/chain", headers={"x-request-id": "example-chain"})

    assert response.status_code == 200
    assert response.json()["results"] == {
        "app_b": {"request_id": "example-chain"},
        "app_c": {"request_id": "example-chain"},
    }


def _background_responses(base_url: str) -> float:
    label = 'path="/lab/background",service="example-test",status_code="202"}'
    return sum(
        float(line.rsplit(" ", 1)[1])
        for line in httpx2.get(f"{base_url}/metrics").text.splitlines()
        if line.startswith("http_requests_total{") and label in line
    )


@pytest.mark.parametrize("fail", [False, True])
def test_background_work_keeps_response_timing_and_later_failure(demo_server, fail) -> None:
    base_url, log_path = demo_server
    before = _background_responses(base_url)
    response = httpx2.get(f"{base_url}/lab/background", params={"seconds": 0.3, "fail": fail})
    assert response.status_code == 202
    record = wait_record(log_path, response.headers["x-request-id"])
    event = record["event"]
    assert event["status_code"] == 202
    assert event["response_completed"] is True
    assert event["app_duration_ms"] - event["duration_ms"] >= 250
    assert record["message"] == ("request.failed" if fail else "request.completed")
    if fail:
        assert record["error"]["type"] == "RuntimeError"
    assert _background_responses(base_url) - before == 1


@pytest.mark.parametrize(
    "failure, timeout, expected_status, errors",
    [
        (False, 1.0, 200, {}),
        (True, 1.0, 502, {"http_5xx": 1}),
        (False, 0.02, 504, {"read_timeout": 1}),
    ],
)
def test_downstream_calls_without_tracing(demo_server, failure, timeout, expected_status, errors):
    base_url, log_path = demo_server
    response = httpx2.get(
        f"{base_url}/lab/downstream",
        params={"seconds": 0.1, "timeout": timeout, "failure": failure},
    )
    assert response.status_code == expected_status
    record = wait_record(log_path, response.headers["x-request-id"])
    assert record["event"]["http"]["count"] == 1
    assert record["event"]["http"]["ms"] >= (10 if timeout < 0.1 else 80)
    assert record["event"]["http"].get("errors", {}) == errors


def test_concurrent_requests_keep_their_own_call_totals(demo_server):
    base_url, log_path = demo_server

    def request(count):
        response = httpx2.get(
            f"{base_url}/lab/downstream", params={"calls": count, "seconds": 0.05}
        )
        assert response.status_code == 200
        return response.headers["x-request-id"], count

    with ThreadPoolExecutor(max_workers=6) as workers:
        results = list(workers.map(request, [1, 2, 3, 1, 2, 3]))
    assert len({request_id for request_id, _ in results}) == 6
    for request_id, count in results:
        event = wait_record(log_path, request_id)["event"]
        assert event["http"]["count"] == count
        assert event["http"]["ms"] >= 40 * count


@pytest.mark.parametrize("timeout", [1.0, 0.02])
def test_streamed_downstream_completion_and_failure(demo_server, timeout):
    base_url, log_path = demo_server
    with httpx2.stream(
        "GET", f"{base_url}/lab/downstream-stream", params={"seconds": 0.1, "timeout": timeout}
    ) as response:
        assert response.status_code == 200
        request_id = response.headers["x-request-id"]
        if timeout < 0.1:
            with pytest.raises(httpx2.HTTPError):
                b"".join(response.iter_bytes())
        else:
            assert b"".join(response.iter_bytes()) == b"first\nlast\n"
    record = wait_record(log_path, request_id)
    assert record["event"]["status_code"] == 200
    assert record["event"]["http"]["count"] == 1
    if timeout < 0.1:
        assert record["message"] == "request.failed"
        assert record["event"]["response_completed"] is False
        assert record["event"]["http"]["errors"] == {"read_timeout": 1}
    else:
        assert record["event"]["response_completed"] is True
        assert record["event"]["duration_ms"] >= 80
        assert record["event"]["http"]["ms"] >= 80


def test_failure_lab_produces_the_named_warnings(demo_server) -> None:
    base_url, log_path = demo_server
    completed = subprocess.run(
        [sys.executable, "examples/full_stack/failure_lab.py", "--base-url", base_url],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert completed.returncode == 0, completed.stderr
    expected = {
        "event_loop.blocked",
        "db.pool_exhausted",
        "db.n_plus_one",
        "db.slow_query",
        "db.connection_held_during_http",
        "db.connection_held_idle",
        "http.n_plus_one",
    }
    assert all(name in completed.stdout for name in expected)
    wait_for(log_path, lambda rows: expected <= {row["message"] for row in rows})


def test_failure_lab_local_mode_runs_standalone() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "examples/full_stack/failure_lab.py",
            "--local",
            "--scenario",
            "n-plus-one",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert completed.returncode == 0, completed.stderr
    assert "db.n_plus_one" in completed.stdout


def test_alerts_and_grafana_dashboard_cover_all_diagnostic_metrics() -> None:
    from fastapiobserver.metrics.prometheus import _DIAGNOSTIC_COUNTERS, _SINK_COUNTERS

    alerts_path = ROOT / "examples/full_stack/config/alerts.yml"
    prometheus_cfg = (ROOT / "examples/full_stack/config/prometheus.yml").read_text(
        encoding="utf-8"
    )
    compose_cfg = (ROOT / "examples/full_stack/docker-compose.yml").read_text(encoding="utf-8")
    dashboard_path = ROOT / "examples/full_stack/config/grafana/dashboards/api-overview.json"

    alerts_text = alerts_path.read_text(encoding="utf-8")
    dashboard_text = dashboard_path.read_text(encoding="utf-8")
    dashboard = json.loads(dashboard_text)

    assert "/etc/prometheus/alerts.yml" in prometheus_cfg
    assert "./config/alerts.yml:/etc/prometheus/alerts.yml:ro" in compose_cfg

    # No overlapping Grafana panel grid rectangles.
    boxes = [
        (
            panel["gridPos"]["x"],
            panel["gridPos"]["y"],
            panel["gridPos"]["x"] + panel["gridPos"]["w"],
            panel["gridPos"]["y"] + panel["gridPos"]["h"],
        )
        for panel in dashboard["panels"]
    ]
    for index, (x1, y1, x2, y2) in enumerate(boxes):
        for ox1, oy1, ox2, oy2 in boxes[index + 1 :]:
            assert x2 <= ox1 or ox2 <= x1 or y2 <= oy1 or oy2 <= y1

    prometheus_source = (ROOT / "src/fastapiobserver/metrics/prometheus.py").read_text(
        encoding="utf-8"
    )
    known_metrics = set(re.findall(r"fastapiobserver_[a-z0-9_]+", prometheus_source))
    known_metrics.update(f"fastapiobserver_sink_circuit_breaker_{key}" for key in _SINK_COUNTERS)

    for text in (alerts_text, dashboard_text):
        referenced = set(re.findall(r"fastapiobserver_[a-z0-9_]+", text))
        assert referenced <= known_metrics

    diagnostic_counters = {spec[0] for spec in _DIAGNOSTIC_COUNTERS.values()}
    assert diagnostic_counters <= set(re.findall(r"fastapiobserver_[a-z0-9_]+", alerts_text))
    assert diagnostic_counters <= set(re.findall(r"fastapiobserver_[a-z0-9_]+", dashboard_text))


def test_batched_query_returns_the_same_data_with_one_query(demo_server) -> None:
    base_url, log_path = demo_server
    with httpx2.Client(base_url=base_url) as client:
        slow = client.get("/lab/n-plus-one")
        fast = client.get("/lab/batched")
    assert slow.status_code == fast.status_code == 200
    assert slow.json() == fast.json()
    assert len(fast.json()) == 10
    for response, queries in ((slow, 11), (fast, 1)):
        record = wait_record(log_path, response.headers["x-request-id"])
        assert record["event"]["db"]["count"] == queries
