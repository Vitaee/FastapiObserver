from __future__ import annotations

import importlib
import logging
import typing

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
def reset_global_state() -> typing.Generator[None, None, None]:
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
    """Shut down and unset the global tracer, logger and meter providers."""
    for module_name, attr in (
        ("opentelemetry.trace", "_TRACER_PROVIDER"),
        ("opentelemetry._logs._internal", "_LOGGER_PROVIDER"),
        ("opentelemetry.metrics._internal", "_METER_PROVIDER"),
    ):
        try:
            module = importlib.import_module(module_name)
        except Exception:
            continue
        provider = getattr(module, attr, None)
        try:
            if callable(getattr(provider, "shutdown", None)):
                provider.shutdown()  # type: ignore[union-attr]
        except Exception:
            pass
        setattr(module, attr, None)
        set_once = getattr(module, f"{attr}_SET_ONCE", None)
        if set_once is not None and hasattr(set_once, "_done"):
            set_once._done = False
