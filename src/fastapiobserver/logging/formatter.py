from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from .._version import __version__
from ..config import ObservabilitySettings
from ..plugins import apply_log_enrichers
from ..request_context import get_request_id, get_span_id, get_trace_id, get_user_context
from ..security import SecurityPolicy, sanitize_event
from ..utils import lazy_import

orjson: Any
try:
    orjson = lazy_import("orjson")
except ModuleNotFoundError:  # pragma: no cover
    orjson = None

LOG_SCHEMA_VERSION = "1.0.0"


class StructuredJsonFormatter(logging.Formatter):
    """Serialize log records into structured JSON.

    Fields include service identity, request ID, trace/span IDs for
    correlation, and user context.  Plugin enrichers are applied unless
    the record has ``_skip_enrichers`` set.
    """

    def __init__(
        self,
        settings: ObservabilitySettings,
        security_policy: SecurityPolicy | None = None,
        *,
        enrich_event: (
            Callable[[dict[str, object]], Mapping[str, object] | dict[str, object]] | None
        ) = None,
        sanitize_payload: (
            Callable[[dict[str, Any], SecurityPolicy], Mapping[str, Any] | dict[str, Any]]
            | None
        ) = None,
    ) -> None:
        super().__init__()
        self.settings = settings
        self.security_policy = security_policy or SecurityPolicy()
        self._enrich_event = enrich_event or apply_log_enrichers
        self._sanitize_payload = sanitize_payload or sanitize_event

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "app_name": self.settings.app_name,
            "service": self.settings.service,
            "environment": self.settings.environment,
            "version": self.settings.version,
            "log_schema_version": LOG_SCHEMA_VERSION,
            "library": "fastapiobserver",
            "library_version": __version__,
        }

        request_id = getattr(record, "request_id", None) or get_request_id()
        if request_id:
            payload["request_id"] = request_id

        trace_id = getattr(record, "trace_id", None) or get_trace_id()
        if trace_id:
            payload["trace_id"] = trace_id

        span_id = getattr(record, "span_id", None) or get_span_id()
        if span_id:
            payload["span_id"] = span_id

        user_context = get_user_context()
        if user_context:
            payload["user_context"] = user_context

        event = getattr(record, "event", None)
        if isinstance(event, dict):
            payload["event"] = event

        if record.exc_info:
            error_payload = _build_structured_error(record, self)
            payload["error"] = error_payload
            # Backward-compatible field retained for existing dashboards.
            payload["exc_info"] = error_payload["stacktrace"]

        if getattr(record, "_skip_enrichers", False):
            enriched_payload = payload
        else:
            candidate_payload = self._enrich_event(dict(payload))
            if isinstance(candidate_payload, Mapping):
                enriched_payload = dict(candidate_payload)
            else:
                enriched_payload = payload
        sanitized_candidate = self._sanitize_payload(enriched_payload, self.security_policy)
        if isinstance(sanitized_candidate, Mapping):
            sanitized_payload = dict(sanitized_candidate)
        else:
            sanitized_payload = enriched_payload
        return _json_dumps(sanitized_payload)


def _json_dumps(payload: dict[str, Any]) -> str:
    if orjson is not None:
        return orjson.dumps(payload).decode("utf-8")
    return json.dumps(payload, ensure_ascii=True, default=str)


def _build_structured_error(
    record: logging.LogRecord,
    formatter: logging.Formatter,
) -> dict[str, str]:
    exc_info = record.exc_info
    if not exc_info:
        return {"type": "", "message": "", "stacktrace": ""}

    if not isinstance(exc_info, tuple) or len(exc_info) != 3:
        return {
            "type": "Exception",
            "message": "",
            "stacktrace": "",
        }

    exc_type, exc_value, _exc_tb = exc_info
    error_type = exc_type.__name__ if exc_type is not None else "Exception"
    error_message = str(exc_value) if exc_value is not None else ""
    stacktrace = formatter.formatException(exc_info)
    fingerprint = _generate_error_fingerprint(error_type, stacktrace)

    return {
        "type": error_type,
        "message": error_message,
        "stacktrace": stacktrace,
        "fingerprint": fingerprint,
    }


def _generate_error_fingerprint(error_type: str, stacktrace: str) -> str:
    """Hash stack trace after stripping out environment-specific noise."""
    if not stacktrace:
        return hashlib.sha256(error_type.encode("utf-8")).hexdigest()[:32]

    # Strip hexadecimal memory addresses
    sanitized = re.sub(r"0x[0-9a-fA-F]+", "0x<ptr>", stacktrace)
    # Strip exact line numbers to survive minor file refactoring
    sanitized = re.sub(r"line \d+", "line <N>", sanitized)
    
    # Combine error type and sanitized trace to ensure distinct groupings
    payload = f"{error_type}:{sanitized}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
