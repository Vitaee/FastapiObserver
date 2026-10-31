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


UNMATCHED_ROUTE = "<unmatched>"


def _extract_route_template(scope: Scope, raw_path: str, status_code: int) -> str:
    """Return the metrics ``path`` label: the matched route template if known.

    After routing, ``scope["route"]`` holds the matched route, whose ``.path``
    is the template (e.g. ``/users/{user_id}``). Without one, a 404 means no
    route matched, mostly scanners, so those share ``UNMATCHED_ROUTE`` instead
    of adding a time series per URL. Other requests without a template keep
    *raw_path*: FastAPI doesn't record plain Starlette routes, such as ones
    added with ``app.add_route()``, in the scope.
    """
    template = getattr(scope.get("route"), "path", None)
    if isinstance(template, str) and template:
        return template
    return UNMATCHED_ROUTE if status_code == 404 else raw_path

__all__ = [
    "UNMATCHED_ROUTE",
    "_RequestEventBuilder",
    "_classify_error",
    "_extract_route_template",
    "_sanitize_exception_message",
]
