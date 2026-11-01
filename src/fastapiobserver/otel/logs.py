"""OTLP log export with sanitization support."""

from __future__ import annotations

import logging
from typing import Any, Callable

from fastapi import FastAPI

from ..config import ObservabilitySettings
from ..security import SecurityPolicy, sanitize_event
from .lifecycle import flush_on_app_shutdown
from .resource import (
    build_otlp_exporter,
    create_otel_resource,
    has_configured_provider,
    import_otel_module,
)
from .settings import OTelLogsSettings, OTelSettings

_LOGGER = logging.getLogger("fastapiobserver.otel")
_OTLP_LOG_PROCESSOR_KEYS_ATTR = "_fastapiobserver_otlp_log_processor_keys"
_STANDARD_LOG_RECORD_ATTRS = frozenset(logging.makeLogRecord({}).__dict__)


class _SanitizingOTLPLogHandler(logging.Handler):
    """Wrap OTLP LoggingHandler and sanitize custom record attributes.

    This ensures OTLP-exported log attributes follow the same security policy
    as structured JSON sink output.
    """

    def __init__(
        self,
        delegate: logging.Handler,
        *,
        security_policy: SecurityPolicy,
    ) -> None:
        super().__init__(level=delegate.level)
        self._delegate = delegate
        self._security_policy = security_policy
        self._otel_context = import_otel_module("opentelemetry.context")
        self._otel_trace = import_otel_module("opentelemetry.trace")

    def emit(self, record: logging.LogRecord) -> None:
        try:
            sanitized_record = logging.makeLogRecord(record.__dict__.copy())
            _sanitize_record_custom_attributes(
                sanitized_record,
                self._security_policy,
            )
            # Records are exported on the queue's listener thread, where no span
            # is current, so OpenTelemetry would give them no trace. Make the
            # span the record was created in current while it's exported.
            span_context = sanitized_record.__dict__.pop("_otel_span_context", None)
            if span_context is None:
                self._delegate.emit(sanitized_record)
                return
            span = self._otel_trace.NonRecordingSpan(span_context)
            token = self._otel_context.attach(self._otel_trace.set_span_in_context(span))
            try:
                self._delegate.emit(sanitized_record)
            finally:
                self._otel_context.detach(token)
        except Exception:
            self.handleError(record)

    def setFormatter(self, fmt: logging.Formatter | None) -> None:  # noqa: N802
        super().setFormatter(fmt)
        self._delegate.setFormatter(fmt)

    def flush(self) -> None:
        self._delegate.flush()

    def close(self) -> None:
        try:
            self._delegate.close()
        finally:
            super().close()


def install_otel_logs(
    settings: ObservabilitySettings,
    otel_logs_settings: OTelLogsSettings,
    *,
    app: FastAPI | None = None,
    otel_settings: OTelSettings | None = None,
    security_policy: SecurityPolicy | None = None,
) -> logging.Handler | None:
    """Configure ``LoggerProvider`` with OTLP export for structured logs.

    Uses the same OTel resource attributes as traces to ensure
    consistent service identity across all signals.

    Returns the ``LoggingHandler`` so that the caller can route it
    through the existing ``QueueListener`` pipeline, ensuring filters
    (request ID, trace context) and sanitization are applied
    consistently to both local JSON and OTLP logs.
    """
    if otel_logs_settings.logs_mode == "local_json":
        return None

    try:
        otel_logs_sdk = import_otel_module("opentelemetry.sdk._logs")
        otel_logs_export = import_otel_module("opentelemetry.sdk._logs.export")
        otel_log_api = import_otel_module("opentelemetry._logs")
        otel_log_handler = import_otel_module("opentelemetry.instrumentation.logging.handler")
    except RuntimeError:
        _LOGGER.warning(
            "otel.logs.sdk_unavailable",
            extra={
                "event": {
                    "message": "OTLP log export requires fastapi-observer[otel].",
                },
                "_skip_enrichers": True,
            },
        )
        return None

    policy = security_policy or SecurityPolicy()
    processor_key = (
        otel_logs_settings.protocol,
        otel_logs_settings.otlp_endpoint or "__default__",
    )

    def build_processor() -> Any:
        return otel_logs_export.BatchLogRecordProcessor(
            build_otlp_exporter("logs", otel_logs_settings)
        )

    logger_provider = otel_log_api.get_logger_provider()
    provider_owned = not has_configured_provider(logger_provider)
    if provider_owned:
        # Re-use resource from trace settings when available.
        trace_otel = otel_settings or OTelSettings(
            service_name=settings.service,
            service_version=settings.version,
            environment=settings.environment,
        )
        logger_provider = otel_logs_sdk.LoggerProvider(
            resource=create_otel_resource(settings, trace_otel)
        )
        if not _attach_log_processor_once(logger_provider, processor_key, build_processor):
            _LOGGER.warning(
                "otel.logs.processor_attach.failed",
                extra={"_skip_enrichers": True},
            )
            return None
        otel_log_api.set_logger_provider(logger_provider)
    elif otel_logs_settings.otlp_endpoint and not _attach_log_processor_once(
        logger_provider, processor_key, build_processor
    ):
        _LOGGER.warning(
            "otel.logs.external_provider_without_processor_hook",
            extra={
                "event": {
                    "provider_class": logger_provider.__class__.__name__,
                },
                "_skip_enrichers": True,
            },
        )

    # Build the handler directly: LoggingInstrumentor().instrument() would also
    # attach it to the root logger, bypassing the queue and redaction. The
    # caller routes this one through the QueueListener pipeline instead.
    raw_handler = otel_log_handler.LoggingHandler(
        level=logging.NOTSET,
        logger_provider=logger_provider,
        log_code_attributes=True,  # the SDK handler always added code.* attributes
    )
    otel_handler = _SanitizingOTLPLogHandler(
        raw_handler,
        security_policy=policy,
    )
    flush_on_app_shutdown(app, logger_provider)
    _LOGGER.info(
        "otel.logs.installed",
        extra={
            "event": {
                "logs_mode": otel_logs_settings.logs_mode,
                "endpoint": otel_logs_settings.otlp_endpoint or "default",
                "provider_class": logger_provider.__class__.__name__,
            },
            "_skip_enrichers": True,
        },
    )
    return otel_handler


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _attach_log_processor_once(
    logger_provider: Any,
    key: tuple[str, str],
    build_processor: Callable[[], Any],
) -> bool:
    """Add an OTLP processor unless one for ``key`` is already attached."""
    keys: set[tuple[str, str]] = getattr(logger_provider, _OTLP_LOG_PROCESSOR_KEYS_ATTR, set())
    if key in keys:
        return True
    try:
        logger_provider.add_log_record_processor(build_processor())
        setattr(logger_provider, _OTLP_LOG_PROCESSOR_KEYS_ATTR, keys | {key})
    except Exception:
        return False
    return True


def _sanitize_record_custom_attributes(
    record: logging.LogRecord,
    policy: SecurityPolicy,
) -> None:
    custom_attributes = {
        key: value
        for key, value in record.__dict__.items()
        if key not in _STANDARD_LOG_RECORD_ATTRS and not key.startswith("_")
    }
    if not custom_attributes:
        return

    sanitized_attributes = sanitize_event(custom_attributes, policy)
    for key in custom_attributes:
        record.__dict__.pop(key, None)
    for key, value in sanitized_attributes.items():
        record.__dict__[key] = value
