from __future__ import annotations

import importlib
import logging
import typing

import pytest

from fastapiobserver.plugins import clear_plugins
from fastapiobserver.logging import shutdown_logging
from fastapiobserver.request_context import (
    clear_request_id,
    clear_span_id,
    clear_trace_id,
    clear_user_context,
)
from fastapiobserver.sinks import clear_sinks
from tests.helpers import reset_otel_providers


@pytest.fixture(autouse=True)
def reset_global_state() -> typing.Generator[None, None, None]:
    root = logging.getLogger()
    original_level = root.level

    clear_plugins()
    clear_sinks()
    clear_request_id()
    clear_trace_id()
    clear_span_id()
    clear_user_context()
    reset_otel_providers()
    textmap = _global_textmap()
    yield
    # Managed queues hold captured streams and SDK handlers from this test.
    # Stop them before another test can reuse those outputs or reset providers.
    shutdown_logging()
    if textmap is not None:
        importlib.import_module("opentelemetry.propagate").set_global_textmap(textmap)
    clear_plugins()
    clear_sinks()
    clear_request_id()
    clear_trace_id()
    clear_span_id()
    clear_user_context()
    reset_otel_providers()
    root.setLevel(original_level)


def _global_textmap() -> typing.Any:
    """OpenTelemetry's global propagator, which install_observability() extends."""
    try:
        return importlib.import_module("opentelemetry.propagate").get_global_textmap()
    except ImportError:
        return None


@pytest.fixture
def span_exporter(monkeypatch: pytest.MonkeyPatch) -> typing.Any:
    in_memory = pytest.importorskip("opentelemetry.sdk.trace.export.in_memory_span_exporter")
    tracing = importlib.import_module("fastapiobserver.otel.tracing")
    exporter = in_memory.InMemorySpanExporter()
    monkeypatch.setattr(tracing, "build_otlp_exporter", lambda *_: exporter)
    return exporter
