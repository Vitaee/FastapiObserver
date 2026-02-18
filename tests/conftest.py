from __future__ import annotations

import logging

import pytest

from fastapiobserver.plugins import clear_plugins
from fastapiobserver.request_context import (
    clear_request_id,
    clear_span_id,
    clear_trace_id,
    clear_user_context,
)

pytest_plugins = ("tests.conftest_otlp",)


@pytest.fixture(autouse=True)
def reset_global_state() -> None:
    root = logging.getLogger()
    original_level = root.level

    clear_plugins()
    clear_request_id()
    clear_trace_id()
    clear_span_id()
    clear_user_context()
    _reset_otel_global_provider()
    yield
    clear_plugins()
    clear_request_id()
    clear_trace_id()
    clear_span_id()
    clear_user_context()
    _reset_otel_global_provider()
    root.setLevel(original_level)


def _reset_otel_global_provider() -> None:
    try:
        from opentelemetry import trace as trace_api  # type: ignore
    except Exception:
        return

    try:
        provider = trace_api.get_tracer_provider()
        shutdown = getattr(provider, "shutdown", None)
        if callable(shutdown):
            shutdown()
    except Exception:
        pass

    try:
        if hasattr(trace_api, "_TRACER_PROVIDER"):
            trace_api._TRACER_PROVIDER = None  # type: ignore[attr-defined]
        set_once = getattr(trace_api, "_TRACER_PROVIDER_SET_ONCE", None)
        if set_once is not None and hasattr(set_once, "_done"):
            set_once._done = False  # type: ignore[attr-defined]
    except Exception:
        pass
