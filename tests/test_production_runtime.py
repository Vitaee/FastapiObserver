"""Real-server release gates. Budgets are documented in docs/deployment.md."""

from __future__ import annotations

import os
import signal
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import httpx2
import pytest

from tests.helpers import free_port, records, subprocess_env, uvicorn_server, wait_record

APP = '''
import asyncio, gc, logging, os, threading, tracemalloc
from fastapi import FastAPI, BackgroundTasks, Request
from fastapi.responses import StreamingResponse
from fastapiobserver import install_observability
from fastapiobserver import ObservabilitySettings
from fastapiobserver.logging import setup_logging, get_log_queue_stats
from fastapiobserver.request_context import get_request_state

tracemalloc.start()
app = FastAPI()
install_observability(app)
log = logging.getLogger("probe")
release_sink = threading.Event()
if os.environ.get("PROBE_BLOCK_SINK"):
    class BlockedSink(logging.Handler):
        def emit(self, record):
            release_sink.wait()
    setup_logging(ObservabilitySettings(log_queue_max_size=20), extra_handlers=[BlockedSink()])

@app.get("/release-sink")
async def unblock():
    release_sink.set()
    return {"ok": True}

@app.get("/health")
async def health():
    gc.collect()
    rss = None
    if os.path.exists("/proc/self/status"):
        for line in open("/proc/self/status"):
            if line.startswith("VmRSS:"):
                rss = int(line.split()[1]) * 1024
    return {"heap": tracemalloc.get_traced_memory()[0], "rss": rss,
            "threads": threading.active_count(), "queue": get_log_queue_stats()}

@app.get("/work")
async def work(seconds: float = 0.2):
    log.warning("probe.started")
    await asyncio.sleep(seconds)
    return {"ok": True}

@app.get("/background")
async def background(tasks: BackgroundTasks):
    log.warning("probe.started")
    tasks.add_task(asyncio.sleep, 0.3)
    return {"ok": True}

@app.get("/stream")
async def stream():
    async def chunks():
        log.warning("probe.started")
        for _ in range(3):
            yield b"chunk\\n"
            await asyncio.sleep(0.1)
    return StreamingResponse(chunks())

@app.get("/disconnect")
async def disconnect(request: Request):
    log.warning("probe.started")
    while not await request.is_disconnected():
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.05)
    return {"ok": True}

@app.get("/churn/{number}")
async def churn(number: int):
    state = get_request_state()
    for _ in range(12):
        state.add_call("db", 0.001, f"SELECT {number}")
    return {"ok": True}
'''


@contextmanager
def server(tmp_path: Path, *, collector_down: bool = False, blocked_sink: bool = False):
    (tmp_path / "probe.py").write_text(APP)
    env = {key: value for key, value in subprocess_env().items()
           if not key.startswith(("OBS_", "OTEL_"))}
    env.update({
        "OBS_OTEL_ENABLED": str(collector_down).lower(),
        "OBS_OTEL_LOGS_ENABLED": str(collector_down).lower(),
        "OBS_OTEL_LOGS_MODE": "both", "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
        "OTEL_EXPORTER_OTLP_ENDPOINT": f"http://127.0.0.1:{free_port()}",
        "OTEL_EXPORTER_OTLP_TIMEOUT": "0.2", "OTEL_BSP_SCHEDULE_DELAY": "100",
        "OTEL_BLRP_SCHEDULE_DELAY": "100",
    })
    if blocked_sink:
        env["PROBE_BLOCK_SINK"] = "1"
    log_path = tmp_path / "server.log"
    with uvicorn_server(
        "probe:app", log_path, cwd=tmp_path, env=env,
        args=("--timeout-graceful-shutdown", "1", "--no-access-log"),
    ) as (process, base):
        yield process, base, log_path


def test_real_disconnect_reports_work_after_observation(tmp_path):
    with server(tmp_path) as (_, base, path):
        port = int(base.rsplit(":", 1)[1])
        with socket.create_connection(("127.0.0.1", port)) as connection:
            connection.sendall(
                b"GET /disconnect HTTP/1.1\r\nHost: localhost\r\nX-Request-ID: left\r\n\r\n"
            )
            wait_record(path, "left", "probe.started")
        event = wait_record(path, "left", "request.client_disconnected")["event"]
        assert event["client_disconnected"] is True
        assert event["cancelled"] is False
        assert event["after_disconnect_ms"] >= 40


@pytest.mark.skipif(os.name == "nt", reason="POSIX SIGTERM contract")
@pytest.mark.parametrize("route", ["work", "stream", "background"])
def test_sigterm_drains_inflight_work_without_telemetry_loss(tmp_path, route):
    with server(tmp_path) as (process, base, path), ThreadPoolExecutor(max_workers=8) as workers:
        with httpx2.Client(base_url=base, timeout=5) as client:
            futures = [workers.submit(client.get, "/" + route + "?seconds=0.4",
                                      headers={"X-Request-ID": f"draining-{i}"}) for i in range(8)]
            for i in range(8):
                wait_record(path, f"draining-{i}", "probe.started")
            started = time.monotonic()
            process.terminate()
            responses = [future.result(timeout=5) for future in futures]
        process.wait(timeout=4)
        assert time.monotonic() - started < 4
        assert process.returncode in (0, -signal.SIGTERM)
        assert all(response.status_code == 200 for response in responses)
        terminal = [r for r in records(path) if r.get("request_id", "").startswith("draining-")
                    and r["message"].startswith("request.")]
        assert len(terminal) == len({r["request_id"] for r in terminal}) == 8
        assert all(r["message"] == "request.completed" for r in terminal)
        assert all(r["event"]["response_completed"] is True for r in terminal)
        for record in terminal:
            event = record["event"]
            assert 0 <= event["response_start_ms"] <= event["first_body_ms"] <= event["duration_ms"]
            assert event["stream_duration_ms"] >= (200 if route == "stream" else 0)


@pytest.mark.skipif(os.name == "nt", reason="POSIX SIGTERM contract")
def test_sigterm_cancellation_does_not_claim_a_client_disconnect(tmp_path):
    with server(tmp_path) as (process, base, path), ThreadPoolExecutor() as workers:
        future = workers.submit(httpx2.get, base + "/work?seconds=30",
                                headers={"X-Request-ID": "cut-off"}, timeout=5)
        wait_record(path, "cut-off", "probe.started")
        process.terminate()
        process.wait(timeout=4)
        assert future.result(timeout=5).status_code == 500  # Uvicorn sends the fallback.
        event = wait_record(path, "cut-off", "request.cancelled")["event"]
        assert event["cancelled"] is True
        assert event["client_disconnected"] is False
        assert event["response_completed"] is False
        assert event["response_start_ms"] is None
        assert event["first_body_ms"] is None
        assert event["stream_duration_ms"] is None


def test_unavailable_collector_preserves_local_logs_and_shutdown_budget(tmp_path):
    with server(tmp_path, collector_down=True) as (process, base, path):
        with httpx2.Client(base_url=base, timeout=2) as client:
            started = time.monotonic()
            for number in range(10):
                assert client.get("/work?seconds=0", headers={
                    "X-Request-ID": f"outage-{number}",
                }).status_code == 200
            assert time.monotonic() - started < 2
        started = time.monotonic()
        process.terminate()
        process.wait(timeout=4)
        assert time.monotonic() - started < 4
        completed = [r for r in records(path) if r["message"] == "request.completed"
                     and r.get("request_id", "").startswith("outage-")]
        assert len(completed) == 10
        assert len({r["request_id"] for r in completed}) == 10


def test_warm_worker_memory_and_threads_remain_bounded(tmp_path):
    with server(tmp_path) as (_, base, _), httpx2.Client(base_url=base, timeout=5) as client:
        for number in range(300):
            assert client.get(f"/churn/{number}").status_code == 200
        before = client.get("/health").json()
        for number in range(300, 1_300):
            assert client.get(f"/churn/{number}").status_code == 200
        after = client.get("/health").json()
        assert after["heap"] - before["heap"] < 8 * 1024 * 1024
        assert after["threads"] <= before["threads"] + 2
        if before["rss"] is not None:
            assert after["rss"] - before["rss"] < 32 * 1024 * 1024


def test_blocked_sink_keeps_request_latency_bounded_and_counts_loss(tmp_path):
    with server(tmp_path, blocked_sink=True) as (_, base, _):
        with httpx2.Client(base_url=base, timeout=2) as client:
            try:
                durations = []
                for _ in range(100):
                    started = time.monotonic()
                    assert client.get("/work?seconds=0").status_code == 200
                    durations.append(time.monotonic() - started)
                assert sorted(durations)[94] < 0.1  # p95 loopback latency below 100 ms.
                assert client.get("/health").json()["queue"]["dropped_total"] > 0
            finally:
                assert client.get("/release-sink").status_code == 200
