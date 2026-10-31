"""What a slow log destination costs the code that logs, with and without the queue.

A handler that takes 1 ms per line, like a slow pipe or a busy disk, first on
the caller's thread and then behind fastapi-observer's log queue. Backs
decision record 0002 (docs/decisions/0002-logs-leave-through-a-queue.md).

    python examples/benchmarks/slow_sink.py [log_calls] 2>/dev/null
"""

from __future__ import annotations

import logging
import sys
import time

from fastapiobserver import ObservabilitySettings
from fastapiobserver.logging import get_log_queue_stats, setup_logging, shutdown_logging


class SlowHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        time.sleep(0.001)


def per_call_us(logger: logging.Logger, calls: int) -> float:
    start = time.perf_counter()
    for _ in range(calls):
        logger.info("x")
    return (time.perf_counter() - start) / calls * 1e6


def main(calls: int) -> None:
    direct = logging.getLogger("bench.direct")
    direct.setLevel(logging.INFO)
    direct.propagate = False
    direct.addHandler(SlowHandler())
    on_caller = per_call_us(direct, calls)

    setup_logging(ObservabilitySettings(log_level="INFO"), extra_handlers=[SlowHandler()])
    behind_queue = per_call_us(logging.getLogger("bench.queued"), calls)
    stats = get_log_queue_stats()
    shutdown_logging()

    # stdout carries the JSON log lines; results go to stderr.
    for line in (
        f"slow handler on the caller's thread:      {on_caller:8.1f} us per log call",
        f"same handler behind the library's queue:  {behind_queue:8.1f} us per log call",
        f"queue: enqueued {stats['enqueued_total']}, dropped {stats['dropped_total']}",
    ):
        print(line, file=sys.stderr)


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 500)
