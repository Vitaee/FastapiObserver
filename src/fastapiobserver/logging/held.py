"""A request's log records, held until it ends and then written or dropped.

Two settings hold records:

- ``OBS_LOG_LEVEL_ON_ERROR``: a request's records below ``OBS_LOG_LEVEL`` are written
  only if it ends in a 5xx or an unhandled exception.
- ``OBS_LOG_SAMPLE_RATE``: a request that isn't sampled has its ``INFO`` and lower
  records written only if it fails or takes ``OBS_LOG_SLOW_REQUEST_MS`` or longer.
  ``WARNING`` and higher are always written.
"""

from __future__ import annotations

import hashlib
import logging

from ..request_context import RequestState, get_request_state
from . import state

# ponytail: fixed cap per request, newest kept; make it a setting if 200 isn't enough.
_MAX_HELD_RECORDS = 200
_LOW_64_BITS = (1 << 64) - 1


class HeldLogFilter(logging.Filter):
    """Holds a request's records instead of passing them on, until it ends.

    It is the queue handler's last filter, so held records already carry their
    request ID and trace context. Outside a request, records below *level* are
    dropped, as the log level would have dropped them. *lowest_level* is
    ``OBS_LOG_LEVEL_ON_ERROR``, or *level* without it.
    """

    def __init__(
        self,
        handler: logging.Handler,
        level: int,
        lowest_level: int,
        sample_rate: float = 1.0,
    ) -> None:
        super().__init__()
        self.handler = handler
        self.level = level
        self.lowest_level = lowest_level
        self.sample_rate = sample_rate
        # The bound OpenTelemetry's TraceIdRatioBased sampler uses for the same ratio.
        self.sample_bound = round(sample_rate * (_LOW_64_BITS + 1))

    def filter(self, record: logging.LogRecord) -> bool:
        request_state = get_request_state()
        if record.levelno >= self.level and (
            request_state is None
            or record.levelno >= logging.WARNING
            or self._sampled(request_state, record)
        ):
            return True
        if request_state is not None:
            request_state.hold_log(record, _MAX_HELD_RECORDS)
        return False

    def _sampled(self, request_state: RequestState, record: logging.LogRecord) -> bool:
        if self.sample_bound > _LOW_64_BITS:
            return True
        if request_state.sampled is None:
            bits = _sampling_bits(record)
            request_state.sampled = bits is None or bits < self.sample_bound
            request_state.sample_probability = 1.0 if bits is None else self.sample_rate
            request_state.sample_reason = "no_identity" if bits is None else "sampled"
        return request_state.sampled


def _sampling_bits(record: logging.LogRecord) -> int | None:
    """The 64 bits that decide whether a record's request is sampled.

    The low half of its trace ID, which OpenTelemetry's TraceIdRatioBased sampler
    compares, so every service that samples logs and traces at the same ratio
    keeps the same requests. Without a trace ID, a hash of the request ID, which
    services forward to each other.
    """
    trace_id = getattr(record, "trace_id", None)
    if isinstance(trace_id, str):
        try:
            return int(trace_id, 16) & _LOW_64_BITS
        except ValueError:
            pass
    request_id = getattr(record, "request_id", None)
    if not request_id:
        return None
    digest = hashlib.blake2b(str(request_id).encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big")


def keep_request_logs(*, failed: bool) -> None:
    """Write the current request's held records and pass its later ones.

    Called when a request fails or is slow. Records held only because they are
    below the log level are written only if it *failed*.
    """
    held_filter = state._HELD_LOG_FILTER
    request_state = get_request_state()
    if held_filter is None or request_state is None:
        return
    request_state.sampled = True
    request_state.retention_reason = "failure" if failed else "slow"
    held, request_state.held_logs = request_state.held_logs, None
    for record in held or ():
        if failed or record.levelno >= held_filter.level:
            # emit(), not handle(): the filters already ran when the record was held.
            held_filter.handler.emit(record)


class LogSamplingFilter(logging.Filter):
    """Capture terminal-record sampling policy before application queues run."""

    def filter(self, record: logging.LogRecord) -> bool:
        event = getattr(record, "event", None)
        if (not isinstance(record.msg, str) or not record.msg.startswith(("request.", "operation."))
                or not isinstance(event, dict)):
            return True
        owner = get_request_state()
        metadata: dict[str, object] = {
            "owner": "application", "inclusion_probability": None, "reason": "unknown",
        }
        if owner is not None and owner.observer_logging and state._QUEUE_LISTENER is not None:
            held = state._HELD_LOG_FILTER
            reason, probability = "all", 1.0
            if owner.retention_reason is not None:
                reason = owner.retention_reason
            elif record.levelno >= logging.WARNING:
                reason = "warning"
            elif held is not None and held.sample_rate < 1:
                held._sampled(owner, record)
                reason = owner.sample_reason or "sampled"
                probability = (owner.sample_probability if owner.sample_probability is not None
                               else held.sample_rate)
            metadata = {"owner": "observer", "inclusion_probability": probability, "reason": reason}
        event["log_sampling"] = metadata
        return True


__all__ = ["HeldLogFilter", "keep_request_logs"]
