from __future__ import annotations

import logging

from ..plugins import apply_log_filters
from ..request_context import get_request_id, get_span_id, get_trace_id

_LOGGER = logging.getLogger("fastapiobserver.logging")


class RequestIdFilter(logging.Filter):
    """Attach request ID from context vars to every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not getattr(record, "request_id", None):
            record.request_id = get_request_id()
        return True


class TraceContextFilter(logging.Filter):
    """Attach OTel trace_id / span_id to every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        trace_id = get_trace_id()
        span_id = get_span_id()

        try:
            from opentelemetry import trace as otel_trace  # type: ignore

            span = otel_trace.get_current_span()
            span_context = span.get_span_context()
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
            record.trace_id = trace_id  # type: ignore[attr-defined]
        if span_id:
            record.span_id = span_id  # type: ignore[attr-defined]
        return True


class PluginLogFilter(logging.Filter):
    """Apply user-registered log filters with fault isolation."""

    def filter(self, record: logging.LogRecord) -> bool:
        return apply_log_filters(record)
