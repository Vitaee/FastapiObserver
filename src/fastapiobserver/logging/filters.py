from __future__ import annotations

import logging
import sys

from ..plugins import apply_log_filters
from ..request_context import get_request_id, get_span_id, get_trace_id, get_user_context
from . import volume

_LOGGER = logging.getLogger("fastapiobserver.logging")


class RequestIdFilter(logging.Filter):
    """Attach request ID, user context and route from context vars to every log record.

    Records are formatted on the queue listener thread, where the request's
    context vars are not visible, so they are captured here.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if not getattr(record, "request_id", None):
            record.request_id = get_request_id()
        if not getattr(record, "user_context", None):
            record.user_context = get_user_context()
        if volume.COUNTING:
            record._obs_route = volume.current_route()
        return True


class TraceContextFilter(logging.Filter):
    """Attach OTel trace_id / span_id to every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        trace_id = get_trace_id()
        span_id = get_span_id()

        # No span can be active unless something already imported the OTel API.
        # Looking it up avoids a failing import (a sys.path scan) on every record.
        otel_trace = sys.modules.get("opentelemetry.trace")
        if otel_trace is not None:
            try:
                span_context = otel_trace.get_current_span().get_span_context()
                if span_context and span_context.is_valid:
                    trace_id = f"{span_context.trace_id:032x}"
                    span_id = f"{span_context.span_id:016x}"
            except Exception:
                _LOGGER.debug(
                    "trace_context_filter.otel_lookup_failed",
                    exc_info=True,
                    extra={"_skip_enrichers": True},
                )

        if trace_id:
            record.trace_id = trace_id
        if span_id:
            record.span_id = span_id
        return True


class PluginLogFilter(logging.Filter):
    """Apply user-registered log filters with fault isolation."""

    def filter(self, record: logging.LogRecord) -> bool:
        return apply_log_filters(record)
