from __future__ import annotations

import logging

class _SpanErrorRecorder:
    """Record unhandled request exceptions on the active OTel span when present."""

    def __init__(self, logger: logging.Logger) -> None:
        self.logger = logger

    def record_exception(self, error: Exception) -> None:
        try:
            from opentelemetry import trace as otel_trace
            from opentelemetry.trace import Status, StatusCode
        except ImportError:
            return

        try:
            span = otel_trace.get_current_span()
            if span is None:
                return
            span_context = span.get_span_context()
            if not span_context or not span_context.is_valid:
                return
            span.record_exception(error)
            span.set_status(Status(StatusCode.ERROR))
        except Exception:
            self.logger.debug(
                "otel.span.exception_record.failed",
                exc_info=True,
                extra={"_skip_enrichers": True},
            )

__all__ = ["_SpanErrorRecorder"]
