"""Behaviour across real processes: a fork, and Prometheus multiprocess workers.

Each scenario runs in a fresh interpreter, so forking never touches pytest's
own threads and prometheus_client picks its multiprocess mode at import time.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _run(script: str, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, **(env or {})},
        check=True,
    )
    return result.stdout


@pytest.mark.skipif(not hasattr(os, "fork"), reason="needs os.fork()")
def test_a_forked_child_keeps_logging_and_does_not_repeat_the_parents_lines() -> None:
    stdout = _run(
        """
        import logging, os
        from fastapiobserver import ObservabilitySettings, setup_logging
        from fastapiobserver.logging import flush_logging

        setup_logging(ObservabilitySettings())
        log = logging.getLogger("fork-test")
        log.info("before fork")
        pid = os.fork()
        if pid == 0:
            log.info("in child")
            flush_logging()
            os._exit(0)
        os.waitpid(pid, 0)
        log.info("in parent")
        flush_logging()
        """
    )

    messages = [json.loads(line)["message"] for line in stdout.splitlines() if line.startswith("{")]
    assert sorted(messages) == ["before fork", "in child", "in parent"]


def test_multiprocess_workers_are_summed_at_metrics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("prometheus_client")
    from fastapiobserver.metrics.prometheus import mount_metrics_endpoint

    worker = """
        import os
        from prometheus_client.multiprocess import mark_process_dead
        from fastapiobserver.metrics import build_metrics_backend

        backend = build_metrics_backend(True, service="svc", environment="test")
        for _ in range(5):
            backend.observe(method="GET", path="/orders", status_code=200, duration_seconds=0.01)
        mark_process_dead(os.getpid())
        """
    for _ in range(12):
        _run(worker, env={"PROMETHEUS_MULTIPROC_DIR": str(tmp_path)})

    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(tmp_path))
    app = FastAPI()
    mount_metrics_endpoint(app)
    body = TestClient(app).get("/metrics").text

    [line] = [
        line
        for line in body.splitlines()
        if line.startswith("http_requests_total{") and 'path="/orders"' in line
    ]
    assert float(line.rsplit(" ", 1)[1]) == 60.0
    [histogram] = [line for line in body.splitlines()
                   if line.startswith("http_request_duration_seconds_count{")
                   and 'path="/orders"' in line]
    assert float(histogram.rsplit(" ", 1)[1]) == 60.0


@pytest.mark.skipif(not hasattr(os, "fork"), reason="needs os.fork()")
def test_child_statistics_never_touch_the_inherited_queue_mutex() -> None:
    stdout = _run(
        """
        import logging, os, signal
        from fastapiobserver import ObservabilitySettings, setup_logging
        from fastapiobserver.logging import get_log_queue_stats, flush_logging
        from fastapiobserver.logging import state

        setup_logging(ObservabilitySettings())
        old_queue = state._QUEUE_LISTENER.queue
        old_queue.mutex.acquire()
        pid = os.fork()
        if pid == 0:
            signal.signal(signal.SIGALRM, lambda *_: os._exit(42))
            signal.alarm(2)
            assert get_log_queue_stats()["queue_size"] == 0
            assert get_log_queue_stats()["enqueued_total"] == 0
            logging.getLogger("child").warning("child statistics work")
            flush_logging()
            os._exit(0)
        old_queue.mutex.release()
        _, status = os.waitpid(pid, 0)
        assert os.waitstatus_to_exitcode(status) == 0
        """
    )
    assert "child statistics work" in stdout
