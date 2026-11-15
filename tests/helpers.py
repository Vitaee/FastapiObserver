from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

import fastapiobserver
from fastapiobserver import ObservabilitySettings
from fastapiobserver.logging import setup_logging

SRC = str(Path(fastapiobserver.__file__).resolve().parents[1])


class Collector(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)
        self.lines.append(self.format(record))

    @property
    def payloads(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.lines]


def capture_logs(
    settings: ObservabilitySettings | None = None, *extra: logging.Handler
) -> Collector:
    """Send the managed pipeline's output, after the queue and formatter, to a collector."""
    collector = Collector()
    setup_logging(
        settings or ObservabilitySettings(),
        logs_mode="otlp",
        extra_handlers=[collector, *extra],
    )
    return collector


def run_lifespan(app: Any) -> None:
    async def cycle() -> None:
        async with app.router.lifespan_context(app):
            pass

    asyncio.run(cycle())


def reset_otel_providers() -> None:
    """Shut down and unset the global tracer, logger and meter providers."""
    for module_name, attr in (
        ("opentelemetry.trace", "_TRACER_PROVIDER"),
        ("opentelemetry._logs._internal", "_LOGGER_PROVIDER"),
        ("opentelemetry.metrics._internal", "_METER_PROVIDER"),
    ):
        try:
            module = importlib.import_module(module_name)
        except Exception:
            continue
        provider = getattr(module, attr, None)
        try:
            if callable(getattr(provider, "shutdown", None)):
                provider.shutdown()  # type: ignore[union-attr]
        except Exception:
            pass
        setattr(module, attr, None)
        set_once = getattr(module, f"{attr}_SET_ONCE", None)
        if set_once is not None and hasattr(set_once, "_done"):
            set_once._done = False


def subprocess_env(**extra: str) -> dict[str, str]:
    return {**os.environ, "PYTHONPATH": SRC, **extra}


def run_python(
    script: str, *args: str, forbidden: tuple[str, ...] = (), **env: str
) -> subprocess.CompletedProcess[str]:
    guard = f"""
import importlib.abc, sys
class Forbidden(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {set(forbidden)!r}:
            raise AssertionError('Unexpected import: ' + fullname)
sys.meta_path.insert(0, Forbidden())
""" if forbidden else ""
    return subprocess.run(
        [sys.executable, "-c", guard + script, *args],
        capture_output=True, text=True, timeout=15, env=subprocess_env(**env),
    )


RUN_EXAMPLE = "import runpy, sys\nrunpy.run_path(sys.argv[1], run_name='__main__')\n"


def records(path: Path) -> list[dict[str, Any]]:
    # A polling read can catch the writer partway through its last line.
    return [json.loads(line) for line in path.read_text().split("\n")[:-1] if line.startswith("{")]


def wait_for(path: Path, check: Callable[[list[dict[str, Any]]], Any], timeout: float = 5) -> Any:
    deadline = time.monotonic() + timeout
    while not (found := check(records(path))) and time.monotonic() < deadline:
        time.sleep(0.01)
    if not found:
        pytest.fail(f"Expected records missing:\n{path.read_text()[-5000:]}")
    return found


def wait_record(path: Path, request_id: str, message: str = "request.") -> dict[str, Any]:
    return wait_for(path, lambda rows: next(
        (r for r in rows if r.get("request_id") == request_id and r["message"].startswith(message)),
        None,
    ))


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextmanager
def uvicorn_server(
    app: str, log_path: Path, *, cwd: Path, env: dict[str, str], args: tuple[str, ...] = ()
) -> Iterator[tuple[subprocess.Popen[bytes], str]]:
    import httpx2

    port = free_port()
    base_url = f"http://127.0.0.1:{port}"
    with log_path.open("w") as output:
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", app, "--host", "127.0.0.1", "--port", str(port),
             *args],
            cwd=cwd, env={**env, "PYTHONUNBUFFERED": "1"}, stdout=output, stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 10
            while True:
                assert process.poll() is None, log_path.read_text()
                try:
                    if httpx2.get(f"{base_url}/health", timeout=0.5).status_code == 200:
                        break
                except httpx2.HTTPError:
                    pass
                if time.monotonic() > deadline:
                    pytest.fail(f"{app} did not become ready:\n{log_path.read_text()}")
                time.sleep(0.02)
            yield process, base_url
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
