"""OTLP log export with sanitization support."""

from __future__ import annotations

import logging
from typing import Any, Callable

from ..config import ObservabilitySettings
from ..security import SecurityPolicy, sanitize_event
from .resource import (
    build_log_exporter,
    create_otel_resource,
    has_configured_logger_provider,
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

    def emit(self, record: logging.LogRecord) -> None:
        try:
            sanitized_record = logging.makeLogRecord(record.__dict__.copy())
            _sanitize_record_custom_attributes(
                sanitized_record,
                self._security_policy,
            )
            self._delegate.emit(sanitized_record)
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
    except RuntimeError:
        _LOGGER.warning(
            "otel.logs.sdk_unavailable",
            extra={
                "event": {
                    "message": "OTLP log export requires opentelemetry-sdk.",
                },
                "_skip_enrichers": True,
            },
        )
        return None

    policy = security_policy or SecurityPolicy()

    # Re-use resource from trace settings when available.
    trace_otel = otel_settings or OTelSettings(
        service_name=settings.service,
        service_version=settings.version,
        environment=settings.environment,
    )
    resource = create_otel_resource(settings, trace_otel)
    processor_key = (
        otel_logs_settings.protocol,
        otel_logs_settings.otlp_endpoint or "__default__",
    )

    logger_provider = otel_log_api.get_logger_provider()
    has_external_provider = has_configured_logger_provider(
        otel_log_api,
        logger_provider,
    )
    if not has_external_provider:
        candidate_provider = otel_logs_sdk.LoggerProvider(resource=resource)
        attached = _attach_log_processor_once(
            candidate_provider,
            processor_key,
            lambda: otel_logs_export.BatchLogRecordProcessor(
                build_log_exporter(otel_logs_settings),
            ),
        )
        if not attached:
            _LOGGER.warning(
                "otel.logs.processor_attach.failed",
                extra={"_skip_enrichers": True},
            )
            return None
        try:
            otel_log_api.set_logger_provider(candidate_provider)
            logger_provider = candidate_provider
        except Exception:
            logger_provider = otel_log_api.get_logger_provider()
            attached = _attach_log_processor_once(
                logger_provider,
                processor_key,
                lambda: otel_logs_export.BatchLogRecordProcessor(
                    build_log_exporter(otel_logs_settings),
                ),
            )
            if not attached:
                _LOGGER.warning(
                    "otel.logs.provider_already_configured",
                    extra={"_skip_enrichers": True},
                )
                return None
    elif otel_logs_settings.otlp_endpoint:
        attached = _attach_log_processor_once(
            logger_provider,
            processor_key,
            lambda: otel_logs_export.BatchLogRecordProcessor(
                build_log_exporter(otel_logs_settings),
            ),
        )
        if not attached:
            _LOGGER.warning(
                "otel.logs.external_provider_without_processor_hook",
                extra={
                    "event": {
                        "provider_class": logger_provider.__class__.__name__,
                    },
                    "_skip_enrichers": True,
                },
            )

    # Return handler for routing through QueueListener instead of attaching
    # directly to root. This keeps a single logging pipeline.
    try:
        raw_handler = otel_logs_sdk.LoggingHandler(
            level=logging.NOTSET,
            logger_provider=logger_provider,
        )
    except Exception:
        _LOGGER.debug(
            "otel.logs.handler_create.failed",
            exc_info=True,
            extra={"_skip_enrichers": True},
        )
        return None

    otel_handler = _SanitizingOTLPLogHandler(
        raw_handler,
        security_policy=policy,
    )
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
    if not hasattr(logger_provider, "add_log_record_processor"):
        return False

    existing_keys: set[tuple[str, str]]
    existing_keys = getattr(logger_provider, _OTLP_LOG_PROCESSOR_KEYS_ATTR, set())
    if not isinstance(existing_keys, set):
        try:
            existing_keys = set(existing_keys)
        except TypeError:
            existing_keys = set()
    if key in existing_keys:
        return True

    try:
        logger_provider.add_log_record_processor(build_processor())
    except Exception:
        return False

    existing_keys.add(key)
    try:
        setattr(logger_provider, _OTLP_LOG_PROCESSOR_KEYS_ATTR, existing_keys)
    except Exception:
        # Some provider implementations may not allow custom attributes.
        pass
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
