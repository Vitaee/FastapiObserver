"""CPU fastapi-observer adds to a request, its log line included.

Calls the app in-process, no server or network, and counts the process's CPU
time until the log queue is empty, so the background thread's JSON formatting
counts too. Prints the median overhead against the same app without
fastapi-observer. Backs decision record 0008
(docs/decisions/0008-one-state-object-per-request.md).

    python examples/benchmarks/request_cpu.py [default|metrics|held] > /dev/null

To compare two versions, run it with ``PYTHONPATH`` set to each one's ``src``.
"""

from __future__ import annotations

import asyncio
import logging
import statistics
import sys
import time
from typing import Any

from fastapi import FastAPI

from fastapiobserver import ObservabilitySettings, install_observability
from fastapiobserver.logging import get_log_queue_stats, shutdown_logging

SCOPE = {
    "type": "http",
    "method": "GET",
    "path": "/items/1",
    "raw_path": b"/items/1",
    "query_string": b"",
    "headers": [],
    "client": ("127.0.0.1", 50000),
    "server": ("testserver", 80),
    "scheme": "http",
    "http_version": "1.1",
    "root_path": "",
}


def build(mode: str | None) -> FastAPI:
    app = FastAPI()

    @app.get("/items/{item_id}")
    async def get_item(item_id: str) -> dict[str, str]:
        logging.getLogger("bench").debug("item read")
        return {}

    if mode is not None:
        settings = ObservabilitySettings(log_level_on_error="DEBUG" if mode == "held" else None)
        install_observability(app, settings, metrics_enabled=mode == "metrics")
    return app


async def receive() -> dict[str, Any]:
    return {"type": "http.request", "body": b"", "more_body": False}


async def send(message: Any) -> None:
    pass


async def cpu_us_per_request(app: FastAPI, requests: int) -> float:
    start = time.process_time()
    for _ in range(requests):
        await app(dict(SCOPE), receive, send)
    while get_log_queue_stats()["queue_size"]:
        time.sleep(0.001)
    return (time.process_time() - start) / requests * 1e6


async def main(mode: str) -> None:
    plain, observed = build(None), build(mode)
    await cpu_us_per_request(observed, 2_000)  # warm up
    await cpu_us_per_request(plain, 2_000)
    overheads = [
        await cpu_us_per_request(observed, 5_000) - await cpu_us_per_request(plain, 5_000)
        for _ in range(9)
    ]
    shutdown_logging()
    print(f"{mode}: {statistics.median(overheads):.1f} us of CPU per request", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "default"))
