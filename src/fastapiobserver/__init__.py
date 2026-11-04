"""The public API. Everything else is importable from its submodule, without
the same promise of stability."""

from importlib import import_module
from typing import TYPE_CHECKING, Any

from ._version import __version__
from .config import ObservabilitySettings
from .logging import StructuredJsonFormatter, setup_logging, shutdown_logging
from .loguru import install_loguru_bridge
from .plugins import register_log_enricher, register_log_filter, register_metric_hook
from .propagation import (
    inject_trace_headers,
    instrument_httpx_client,
    instrument_requests_session,
)
from .request_context import get_request_id, get_span_id, get_trace_id, set_user_context
from .security import SecurityPolicy, TrustedProxyPolicy
from .sinks import LogSink, register_sink

if TYPE_CHECKING:
    from .control_plane import RuntimeControlSettings
    from .fastapi import install_observability, observability_lifespan
    from .metrics import mark_prometheus_process_dead
    from .otel import OTelLogsSettings, OTelMetricsSettings, OTelSettings

_FRAMEWORK_EXPORTS = {
    "RuntimeControlSettings": ".control_plane",
    "install_observability": ".fastapi",
    "observability_lifespan": ".fastapi",
    "mark_prometheus_process_dead": ".metrics",
    "OTelLogsSettings": ".otel",
    "OTelMetricsSettings": ".otel",
    "OTelSettings": ".otel",
}


def __getattr__(name: str) -> Any:
    module = _FRAMEWORK_EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module, __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))

__all__ = [
    "__version__",
    # Setup
    "install_observability",
    "observability_lifespan",
    # Settings
    "ObservabilitySettings",
    "OTelLogsSettings",
    "OTelMetricsSettings",
    "OTelSettings",
    "RuntimeControlSettings",
    "SecurityPolicy",
    "TrustedProxyPolicy",
    # Request context
    "get_request_id",
    "get_span_id",
    "get_trace_id",
    "set_user_context",
    # Outbound calls
    "inject_trace_headers",
    "instrument_httpx_client",
    "instrument_requests_session",
    # Logging outside install_observability()
    "StructuredJsonFormatter",
    "setup_logging",
    "shutdown_logging",
    # Extending
    "LogSink",
    "register_log_enricher",
    "register_log_filter",
    "register_metric_hook",
    "register_sink",
    "install_loguru_bridge",
    # Gunicorn multiprocess metrics
    "mark_prometheus_process_dead",
]
