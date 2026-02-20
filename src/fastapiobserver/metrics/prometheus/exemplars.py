from __future__ import annotations

def _get_trace_exemplar() -> dict[str, str] | None:
    """Extract TraceID from the current OTel span for Prometheus exemplar.

    Returns ``None`` when OTel is not installed or no valid span exists.
    """
    try:
        from opentelemetry import trace as otel_trace

        span = otel_trace.get_current_span()
        ctx = span.get_span_context()
        if ctx and ctx.is_valid:
            return {"TraceID": f"{ctx.trace_id:032x}"}
    except Exception:
        pass
    return None

__all__ = ["_get_trace_exemplar"]
