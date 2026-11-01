"""The public API. Everything else is importable from its submodule, without
the same promise of stability."""

from ._version import __version__
from .config import ObservabilitySettings
from .control_plane import RuntimeControlSettings
from .fastapi import install_observability, observability_lifespan
from .logging import StructuredJsonFormatter, setup_logging, shutdown_logging
from .loguru import install_loguru_bridge
from .metrics import mark_prometheus_process_dead
from .otel import OTelLogsSettings, OTelMetricsSettings, OTelSettings
from .plugins import register_log_enricher, register_log_filter, register_metric_hook
from .propagation import (
    inject_trace_headers,
    instrument_httpx_client,
    instrument_requests_session,
)
from .request_context import get_request_id, get_span_id, get_trace_id, set_user_context
from .security import SecurityPolicy, TrustedProxyPolicy
from .sinks import LogSink, register_sink

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
