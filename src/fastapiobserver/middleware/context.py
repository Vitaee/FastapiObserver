from __future__ import annotations

import re
import uuid

from ..config import ObservabilitySettings
from ..request_context import (
    clear_request_id,
    clear_span_id,
    clear_trace_id,
    clear_user_context,
    set_request_id,
    set_span_id,
    set_trace_id,
)
from .headers import _get_header

REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
TRACEPARENT_PATTERN = re.compile(
    r"^[\da-f]{2}-([\da-f]{32})-([\da-f]{16})-[\da-f]{2}$",
    re.IGNORECASE,
)

def _parse_traceparent(traceparent: str | None) -> tuple[str | None, str | None]:
    if not traceparent:
        return None, None
    match = TRACEPARENT_PATTERN.match(traceparent)
    if not match:
        return None, None
    return match.group(1), match.group(2)


def _resolve_request_id(candidate: str | None, trust_candidate: bool) -> str:
    if trust_candidate and candidate and REQUEST_ID_PATTERN.match(candidate):
        return candidate
    return str(uuid.uuid4())

class _RequestContextManager:
    def __init__(self, settings: ObservabilitySettings) -> None:
        self.settings = settings
        self._request_id_header_bytes = settings.request_id_header.lower().encode("latin1")

    def initialize(self, headers: list[tuple[bytes, bytes]], trusted_source: bool) -> str:
        request_id_header = _get_header(headers, self._request_id_header_bytes)
        request_id = _resolve_request_id(request_id_header, trusted_source)
        set_request_id(request_id)

        if trusted_source:
            traceparent = _get_header(headers, b"traceparent")
            trace_id, span_id = _parse_traceparent(traceparent)
            if trace_id:
                set_trace_id(trace_id)
            if span_id:
                set_span_id(span_id)
        return request_id

    def cleanup(self) -> None:
        clear_request_id()
        clear_trace_id()
        clear_span_id()
        clear_user_context()

__all__ = ["_RequestContextManager", "_parse_traceparent", "_resolve_request_id"]
