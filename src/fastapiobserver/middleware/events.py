from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.routing import Match
from starlette.types import Scope

from ..request_context import UNMATCHED_ROUTE, route_template
from ..security import SecurityPolicy, sanitize_event
from ..security.redaction import redact_body

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
        calls: Mapping[str, list[float]] | None = None,
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
        # Redaction below matches keys, and a body is one string value.
        if request_body is not None:
            event["request_body"] = redact_body(request_body, self.policy)
        if response_body is not None:
            event["response_body"] = redact_body(response_body, self.policy)
        if exception is not None:
            event["exception_class"] = exception.__class__.__name__
            event["exception_message"] = _sanitize_exception_message(str(exception))
        for kind, (count, seconds) in (calls or {}).items():
            event[kind] = {"count": int(count), "ms": round(seconds * 1000, 3)}
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


def _extract_route_template(scope: Scope) -> str:
    """Return the metrics ``path`` label: the matched route template if known.

    After routing, the scope holds the matched route's template (e.g.
    ``/users/{user_id}``). FastAPI doesn't record plain Starlette routes, such
    as ones added with ``app.add_route()``, so those are matched here. Anything
    else reached no route, such as scanners, CORS preflights or requests an auth
    middleware rejected, and shares ``UNMATCHED_ROUTE`` instead of adding a time
    series per URL.
    """
    template = route_template(scope)
    if template:
        return template
    for route in getattr(scope.get("app"), "routes", ()):
        path = getattr(route, "path", None)
        if path and route.matches(scope)[0] is not Match.NONE:
            return str(path)
    return UNMATCHED_ROUTE

__all__ = [
    "UNMATCHED_ROUTE",
    "_RequestEventBuilder",
    "_classify_error",
    "_extract_route_template",
    "_sanitize_exception_message",
]
