"""Log the blocking call when something stops the event loop.

A sync call inside ``async def``, such as ``requests.get``, ``time.sleep`` or a
sync database driver, stops every request on the worker until it returns.
asyncio's debug mode reports the slow callback afterwards, without the line that
blocked. This thread reads the loop thread's stack while it is still blocked.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
import time
import traceback

_LOGGER = logging.getLogger("fastapiobserver.event_loop")
# Innermost frames only; the outer ones are the server and the middleware.
_STACK_LIMIT = 30


class EventLoopWatchdog:
    """Sends the running loop a callback and logs ``event_loop.blocked`` if it
    hasn't run after *threshold_seconds*, once per block.

    Create it on the loop's thread, as the app's lifespan does.
    """

    def __init__(self, threshold_seconds: float) -> None:
        self._loop = asyncio.get_running_loop()
        self._loop_thread_id = threading.get_ident()
        self._threshold = threshold_seconds
        self._sent_at: float | None = None  # when the unanswered callback was sent
        self._stopped = threading.Event()
        self._thread = threading.Thread(
            target=self._watch, name="fastapiobserver-loop-watchdog", daemon=True
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stopped.set()
        self._thread.join(timeout=1.0)

    def _answer(self) -> None:
        self._sent_at = None

    def _watch(self) -> None:
        reported = False
        while not self._stopped.wait(self._threshold / 2):
            sent_at = self._sent_at
            if sent_at is None:
                reported = False
                self._sent_at = time.monotonic()
                try:
                    self._loop.call_soon_threadsafe(self._answer)
                except RuntimeError:  # the loop is closed
                    return
            elif not reported and time.monotonic() - sent_at >= self._threshold:
                reported = True
                self._report(time.monotonic() - sent_at)

    def _report(self, blocked_seconds: float) -> None:
        frame = sys._current_frames().get(self._loop_thread_id)
        stack = "".join(traceback.format_stack(frame, limit=_STACK_LIMIT)) if frame else None
        _LOGGER.warning(
            "event_loop.blocked",
            extra={
                "event": {"blocked_ms": round(blocked_seconds * 1000), "stack": stack},
                "_skip_enrichers": True,
            },
        )


__all__ = ["EventLoopWatchdog"]
