from __future__ import annotations

import re
from typing import Any
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import Scope

from ..security import SecurityPolicy, sanitize_event

_CREDENTIAL_RE = re.compile(r"://[^:]+:[^@]+@")


def _sanitize_exception_message(msg: str, *, max_length: int = 512) -> str:
    """Strip credential patterns (e.g. ``://user:pass@``) from exception text."""
    return _CREDENTIAL_RE.sub("://***:***@", msg)[:max_length]

class _RequestEventBuilder:
    def __init__(self, policy: SecurityPolicy) -> None:
        self.policy = policy

    def build(
        self,
        *,
        method: str,
        path: str,
        status_code: int,
        duration_seconds: float,
        client_ip: str | None,
        request_body: str | None,
        response_body: str | None,
        error_type: str,
        exception: Exception | None,
    ) -> dict[str, Any]:
        event: dict[str, Any] = {
            "method": method,
            "path": path,
            "status_code": status_code,
            "duration_ms": round(duration_seconds * 1000, 3),
            "client_ip": client_ip,
            "error_type": error_type,
            # OpenTelemetry semantic-convention aliases for interoperability.
            "http.request.method": method,
            "url.path": path,
            "http.response.status_code": status_code,
        }
        if request_body is not None:
            event["request_body"] = request_body
        if response_body is not None:
            event["response_body"] = response_body
        if exception is not None:
            event["exception_class"] = exception.__class__.__name__
            event["exception_message"] = _sanitize_exception_message(str(exception))
        return sanitize_event(event, self.policy)


def _classify_error(status_code: int, error: Exception | None) -> str:
    if error is not None:
        if isinstance(error, StarletteHTTPException):
            if error.status_code >= 500:
                return "server_error_exception"
            if error.status_code >= 400:
                return "client_error_exception"
        return "unhandled_exception"
    if status_code >= 500:
        return "server_error"
    if status_code >= 400:
        return "client_error"
    return "ok"


def _extract_route_template(scope: Scope, raw_path: str) -> str:
    """Return the matched route template for bounded metric cardinality.

    After Starlette's router runs, ``scope["route"]`` contains the matched
    ``Route`` object whose ``.path`` is the template string
    (e.g. ``/users/{user_id}``).

    Falls back to *raw_path* when:
    * No route matched (404 / unmatched sub-apps)
    * ``scope["route"]`` is not a Starlette ``Route`` with a ``path`` attr
    """
    route = scope.get("route")
    if route is not None:
        template = getattr(route, "path", None)
        if template:
            return template
    return raw_path

__all__ = ["_RequestEventBuilder", "_classify_error", "_extract_route_template", "_sanitize_exception_message"]
