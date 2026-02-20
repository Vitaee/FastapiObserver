from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Literal

CircuitBreakerState = Literal["closed", "open", "half_open"]


@dataclass(frozen=True)
class SinkCircuitBreakerSnapshot:
    sink_name: str
    state: CircuitBreakerState
    failure_threshold: int
    recovery_timeout_seconds: float
    consecutive_failures: int
    handled_total: int
    failures_total: int
    skipped_total: int
    opens_total: int
    half_open_total: int
    closes_total: int

    def as_dict(self) -> dict[str, int | float | str]:
        return {
            "sink_name": self.sink_name,
            "state": self.state,
            "failure_threshold": self.failure_threshold,
            "recovery_timeout_seconds": self.recovery_timeout_seconds,
            "consecutive_failures": self.consecutive_failures,
            "handled_total": self.handled_total,
            "failures_total": self.failures_total,
            "skipped_total": self.skipped_total,
            "opens_total": self.opens_total,
            "half_open_total": self.half_open_total,
            "closes_total": self.closes_total,
        }


class SinkCircuitBreakerHandler(logging.Handler):
    """Protect sink handlers with a basic open/half-open/closed breaker."""

    def __init__(
        self,
        *,
        sink_name: str,
        delegate: logging.Handler,
        failure_threshold: int,
        recovery_timeout_seconds: float,
        clock: Callable[[], float] | None = None,
    ) -> None:
        super().__init__(level=delegate.level)
        self.sink_name = sink_name
        self._delegate = delegate
        self._failure_threshold = max(1, int(failure_threshold))
        self._recovery_timeout_seconds = max(0.001, float(recovery_timeout_seconds))
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()

        self._state: CircuitBreakerState = "closed"
        self._opened_until = 0.0
        self._consecutive_failures = 0
        self._handled_total = 0
        self._failures_total = 0
        self._skipped_total = 0
        self._opens_total = 0
        self._half_open_total = 0
        self._closes_total = 0

    def emit(self, record: logging.LogRecord) -> None:
        if self._should_skip():
            return

        try:
            self._delegate.handle(record)
        except Exception:
            self._record_failure()
            self.handleError(record)
            return

        self._record_success()

    def setFormatter(self, fmt: logging.Formatter | None) -> None:  # noqa: N802
        super().setFormatter(fmt)
        self._delegate.setFormatter(fmt)

    def flush(self) -> None:
        self._delegate.flush()

    def close(self) -> None:
        try:
            self._delegate.close()
        finally:
            super().close()

    def snapshot(self) -> SinkCircuitBreakerSnapshot:
        with self._lock:
            return SinkCircuitBreakerSnapshot(
                sink_name=self.sink_name,
                state=self._state,
                failure_threshold=self._failure_threshold,
                recovery_timeout_seconds=self._recovery_timeout_seconds,
                consecutive_failures=self._consecutive_failures,
                handled_total=self._handled_total,
                failures_total=self._failures_total,
                skipped_total=self._skipped_total,
                opens_total=self._opens_total,
                half_open_total=self._half_open_total,
                closes_total=self._closes_total,
            )

    def _should_skip(self) -> bool:
        with self._lock:
            now = self._clock()
            if self._state != "open":
                return False
            if now >= self._opened_until:
                self._state = "half_open"
                self._half_open_total += 1
                return False
            self._skipped_total += 1
            return True

    def _record_failure(self) -> None:
        with self._lock:
            now = self._clock()
            self._failures_total += 1
            self._consecutive_failures += 1

            should_open = (
                self._state == "half_open"
                or self._consecutive_failures >= self._failure_threshold
            )
            if should_open:
                self._state = "open"
                self._opens_total += 1
                self._opened_until = now + self._recovery_timeout_seconds
                self._consecutive_failures = 0

    def _record_success(self) -> None:
        with self._lock:
            self._handled_total += 1
            self._consecutive_failures = 0
            if self._state == "half_open":
                self._state = "closed"
                self._closes_total += 1
                self._opened_until = 0.0


def get_sink_circuit_breaker_stats() -> dict[str, dict[str, int | float | str]]:
    """Return per-sink circuit-breaker snapshots."""
    from .state import _LOGGING_LOCK, _SINK_CIRCUIT_BREAKERS

    with _LOGGING_LOCK:
        breakers = list(_SINK_CIRCUIT_BREAKERS)
    return {breaker.sink_name: breaker.snapshot().as_dict() for breaker in breakers}
