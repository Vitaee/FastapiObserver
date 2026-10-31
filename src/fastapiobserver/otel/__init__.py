"""OpenTelemetry integration for fastapiobserver.

This subpackage is split by responsibility:

- ``settings`` — configuration models and env-based loading
- ``resource`` — OTel resource creation, URL exclusion, and helpers
- ``tracing`` — ``install_otel()`` for trace instrumentation
- ``logs`` — ``install_otel_logs()`` for OTLP log export
- ``metrics`` — ``install_otel_metrics()`` for OTLP metrics export

All public symbols are re-exported here so that existing imports from
``fastapiobserver.otel`` continue to work.
"""

from . import logs as _logs
from . import metrics as _metrics
from . import resource as _resource
from . import settings as _settings
from . import tracing as _tracing

# Public API
OTEL_PROTOCOLS = _settings.OTEL_PROTOCOLS
OTelSettings = _settings.OTelSettings
OTelLogsSettings = _settings.OTelLogsSettings
OTelMetricsSettings = _settings.OTelMetricsSettings
get_trace_sampling_ratio = _settings.get_trace_sampling_ratio
set_trace_sampling_ratio = _settings.set_trace_sampling_ratio
parse_resource_attributes = _settings.parse_resource_attributes

create_otel_resource = _resource.create_otel_resource
build_excluded_urls_csv = _resource.build_excluded_urls_csv
normalize_otlp_endpoint = _resource.normalize_otlp_endpoint
normalize_otlp_metrics_endpoint = _resource.normalize_otlp_metrics_endpoint

install_otel = _tracing.install_otel
install_otel_logs = _logs.install_otel_logs
install_otel_metrics = _metrics.install_otel_metrics

__all__ = [
    "OTelSettings",
    "OTelLogsSettings",
    "OTelMetricsSettings",
    "OTEL_PROTOCOLS",
    "create_otel_resource",
    "install_otel",
    "install_otel_logs",
    "install_otel_metrics",
    "get_trace_sampling_ratio",
    "set_trace_sampling_ratio",
    "build_excluded_urls_csv",
    "normalize_otlp_endpoint",
    "normalize_otlp_metrics_endpoint",
    "parse_resource_attributes",
]
