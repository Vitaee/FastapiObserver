"""OpenTelemetry integration for fastapiobserver.

This subpackage is split by responsibility:

- ``settings`` — configuration models and env-based loading
- ``resource`` — OTel resource creation, URL exclusion, and helpers
- ``tracing`` — ``install_otel()`` for trace instrumentation
- ``logs`` — ``install_otel_logs()`` for OTLP log export

All public symbols are re-exported here so that existing imports from
``fastapiobserver.otel`` continue to work.
"""

from . import logs as _logs
from . import resource as _resource
from . import settings as _settings
from . import tracing as _tracing

# Public API
OTEL_PROTOCOLS = _settings.OTEL_PROTOCOLS
OTelSettings = _settings.OTelSettings
OTelLogsSettings = _settings.OTelLogsSettings
get_trace_sampling_ratio = _settings.get_trace_sampling_ratio
set_trace_sampling_ratio = _settings.set_trace_sampling_ratio
parse_resource_attributes = _settings.parse_resource_attributes

create_otel_resource = _resource.create_otel_resource
build_excluded_urls_csv = _resource.build_excluded_urls_csv
normalize_otlp_endpoint = _resource.normalize_otlp_endpoint

install_otel = _tracing.install_otel
install_otel_logs = _logs.install_otel_logs

# Backward-compat private aliases from pre-split monolithic ``otel.py``.
_SanitizingOTLPLogHandler = _logs._SanitizingOTLPLogHandler
_OTelEnvSettings = _settings._OTelEnvSettings
_OTelLogsEnvSettings = _settings._OTelLogsEnvSettings
_build_log_exporter = _resource.build_log_exporter
_build_excluded_urls_csv = _resource.build_excluded_urls_csv
_build_span_exporter = _resource.build_span_exporter
_import_otel_module = _resource.import_otel_module
_has_configured_tracer_provider = _resource.has_configured_tracer_provider
_has_configured_logger_provider = _resource.has_configured_logger_provider
_attach_log_processor_once = _logs._attach_log_processor_once
_sanitize_record_custom_attributes = _logs._sanitize_record_custom_attributes
_parse_resource_attributes = _settings.parse_resource_attributes
_normalize_otlp_endpoint = _resource.normalize_otlp_endpoint

__all__ = [
    "OTelSettings",
    "OTelLogsSettings",
    "OTEL_PROTOCOLS",
    "create_otel_resource",
    "install_otel",
    "install_otel_logs",
    "get_trace_sampling_ratio",
    "set_trace_sampling_ratio",
    "build_excluded_urls_csv",
    "parse_resource_attributes",
]
