"""Record a custom client's callback with only the standard library.

Run: python examples/callback_operation.py
The start handle includes executor queueing in the logical call duration.
"""

from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import closing
import logging
import sqlite3

from fastapiobserver import ObservabilitySettings, setup_logging
from fastapiobserver.experimental import observe_operation
from fastapiobserver.logging import flush_logging, shutdown_logging


def read_custom_store() -> int:
    with closing(sqlite3.connect(":memory:")) as connection:
        row = connection.execute("SELECT 42").fetchone()
        assert row is not None
        return int(row[0])


def main() -> None:
    setup_logging(ObservabilitySettings(service="catalog-worker"))
    try:
        with observe_operation("catalog.read") as operation:
            call = operation.start_call("db")

            def completed(future: Future[int]) -> None:
                error = ("cancelled" if future.cancelled()
                         else "query_error" if future.exception() is not None else None)
                call.finish(error=error)

            # Join the executor/callbacks before ending the operation. No copied
            # ContextVar is needed because the handle retains explicit ownership.
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(read_custom_store)
                future.add_done_callback(completed)
                value = future.result()
            logging.getLogger("catalog").info("catalog.read", extra={"event": {"value": value}})
        assert flush_logging()
    finally:
        shutdown_logging()


if __name__ == "__main__":
    main()
