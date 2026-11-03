"""CPU fastapi-observer adds to a request, its log line included.

Calls the app in-process, no server or network, and counts the process's CPU
time until the log queue is empty, so the background thread's JSON formatting
counts too. Prints the median overhead against the same app without
fastapi-observer. Backs decision records 0008
(docs/decisions/0008-one-state-object-per-request.md) and 0010, whose traced
modes trace every request with FastAPI's own OpenTelemetry or with the
instrumentation, and discard the spans, and 0014, whose watchdog mode runs the
event-loop watchdog at the production profile's 250 ms.

    python examples/benchmarks/request_cpu.py MODE [absolute] > /dev/null

MODE is default, metrics, held, sampled, traced, traced-instrumentation or watchdog.
With ``absolute``, it prints the observed app's own CPU per request instead: the
held mode lowers the root logger's level for every app in the process, so the
plain app pays for the ``DEBUG`` records too and the difference hides their cost.
``DEBUG_LINES`` sets how many ``DEBUG`` lines the endpoint writes (default 1).

To compare two versions, run it with ``PYTHONPATH`` set to each one's ``src``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import statistics
import sys
import time
from typing import Any

from fastapi import FastAPI

from fastapiobserver import ObservabilitySettings, OTelSettings, install_observability
from fastapiobserver.logging import get_log_queue_stats, shutdown_logging
from fastapiobserver.loop_watchdog import EventLoopWatchdog

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
# Settings per mode; "metrics" also turns Prometheus on.
MODES: dict[str, dict[str, Any]] = {
    "default": {},
    "metrics": {},
    "held": {"log_level_on_error": "DEBUG"},
    "sampled": {"log_sample_rate": 0.1},
    "traced": {},
    "traced-instrumentation": {},
    "watchdog": {},
}
# FastAPI(telemetry=...) per mode; the rest keep FastAPI's own telemetry off,
# so the global tracer provider a traced mode sets doesn't trace them.
TELEMETRY: dict[str, dict[str, Any]] = {"traced": {}, "traced-instrumentation": {"tracing": False}}
UNTRACED = {"tracing": False, "metrics": False, "logs": False}
DEBUG_LINES = int(os.environ.get("DEBUG_LINES", "1"))


def build(mode: str | None) -> FastAPI:
    app = FastAPI(telemetry=TELEMETRY.get(mode or "", UNTRACED))  # type: ignore[arg-type]

    @app.get("/items/{item_id}")
    async def get_item(item_id: str) -> dict[str, str]:
        for _ in range(DEBUG_LINES):
            logging.getLogger("bench").debug("item read")
        return {}

    if mode is not None:
        install_observability(
            app,
            ObservabilitySettings(**MODES[mode]),
            metrics_enabled=mode == "metrics",
            otel_settings=OTelSettings(enabled=mode in TELEMETRY),
        )
    return app


async def receive() -> dict[str, Any]:
    return {"type": "http.request", "body": b"", "more_body": False}


async def send(message: Any) -> None:
    pass


async def cpu_us_per_request(app: FastAPI, requests: int) -> tuple[float, float]:
    """CPU per request on the calling thread, and in the whole process once the
    log queue is empty, which adds the background thread's formatting."""
    process_start, thread_start = time.process_time(), time.thread_time()
    for _ in range(requests):
        await app(dict(SCOPE), receive, send)
    thread_used = time.thread_time() - thread_start
    while get_log_queue_stats()["queue_size"]:
        time.sleep(0.001)
    process_used = time.process_time() - process_start
    return thread_used / requests * 1e6, process_used / requests * 1e6


def report(mode: str, runs: list[tuple[float, float]]) -> None:
    thread = statistics.median(run[0] for run in runs)
    process = statistics.median(run[1] for run in runs)
    print(
        f"{mode}: {process:.1f} us of CPU per request, {thread:.1f} on the request's thread",
        file=sys.stderr,
    )


def discard_spans() -> None:
    import fastapiobserver.otel.tracing as tracing
    from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

    class DiscardSpans(SpanExporter):
        def export(self, spans: Any) -> SpanExportResult:
            return SpanExportResult.SUCCESS

    tracing.build_otlp_exporter = lambda *_: DiscardSpans()  # type: ignore[assignment]


async def main(mode: str, absolute: bool) -> None:
    if mode in TELEMETRY:
        discard_spans()
    if mode == "watchdog":  # the lifespan starts it, and this harness runs none
        EventLoopWatchdog(0.25).start()
    if absolute:
        observed = build(mode)
        await cpu_us_per_request(observed, 2_000)  # warm up
        runs = [await cpu_us_per_request(observed, 5_000) for _ in range(9)]
        shutdown_logging()
        report(mode, runs)
        return
    plain, observed = build(None), build(mode)
    await cpu_us_per_request(observed, 2_000)  # warm up
    await cpu_us_per_request(plain, 2_000)
    overheads = []
    for _ in range(9):
        observed_run = await cpu_us_per_request(observed, 5_000)
        plain_run = await cpu_us_per_request(plain, 5_000)
        overheads.append((observed_run[0] - plain_run[0], observed_run[1] - plain_run[1]))
    shutdown_logging()
    report(mode, overheads)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "default", "absolute" in sys.argv[2:]))
