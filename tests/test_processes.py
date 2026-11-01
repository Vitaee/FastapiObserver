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
        from fastapiobserver.metrics import build_metrics_backend

        backend = build_metrics_backend(True, service="svc", environment="test")
        for _ in range(5):
            backend.observe(method="GET", path="/orders", status_code=200, duration_seconds=0.01)
        """
    for _ in range(3):
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
    assert float(line.rsplit(" ", 1)[1]) == 15.0
