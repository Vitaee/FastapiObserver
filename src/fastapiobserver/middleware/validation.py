"""Record 422 request validation error locations and types without retaining values."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from functools import wraps
import inspect
import threading
from typing import Any

from fastapi import FastAPI
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from starlette.requests import Request

from ..diagnostics import report_diagnostic
from ..request_context import get_request_state
from .events import _extract_route_template

_WRAPPED = "_fastapiobserver_validation_wrapped"
_MAX_VALIDATION_ERRORS = 16
_MAX_VALIDATION_FIELDS = 256
_FIELDS_LOCK = threading.Lock()
_known_fields: set[str] = set()


def _join_loc(parts: Sequence[Any], *, collapse_indices: bool) -> str:
    out: list[str] = []
    for part in parts:
        if isinstance(part, int) and not isinstance(part, bool):
            out.append("[]" if collapse_indices else f"[{part}]")
        elif segment := str(part)[:64]:
            out.append(f".{segment}" if out else segment)
    return "".join(out)[:200] or "<unknown>"


def _format_loc(loc: Any) -> tuple[str, str]:
    """Return ``(log_loc, metric_field)`` with list indices kept for logs and
    collapsed to ``[]`` for metric labels."""
    parts = loc if isinstance(loc, (tuple, list)) else (loc,)
    return (
        _join_loc(parts, collapse_indices=False),
        _join_loc(parts, collapse_indices=True),
    )


def _bounded_field_label(field: str) -> str:
    with _FIELDS_LOCK:
        if field in _known_fields:
            return field
        if len(_known_fields) >= _MAX_VALIDATION_FIELDS:
            return "<other>"
        _known_fields.add(field)
        return field


def _record_validation_errors(request: Request, exc: RequestValidationError) -> None:
    try:
        raw_errors = exc.errors()
        if not isinstance(raw_errors, Sequence):
            return
        state = get_request_state()
        route = _extract_route_template(request.scope, state)
        details: list[dict[str, str]] = []
        for item in raw_errors[:_MAX_VALIDATION_ERRORS]:
            if not isinstance(item, Mapping):
                continue
            log_loc, metric_field = _format_loc(item.get("loc", ()))
            err_type = str(item.get("type") or "validation_error")[:64]
            details.append({"loc": log_loc, "type": err_type})
            report_diagnostic(
                "http.validation_error",
                (route, _bounded_field_label(metric_field)),
            )
        if state is not None and details and not state.closed:
            state.validation_errors = details
    except Exception:
        # Diagnostics must never fail the exception handler they observe.
        pass


def _wrap_validation_handler(handler: Callable[..., Any]) -> Callable[..., Any]:
    if getattr(handler, _WRAPPED, False):
        return handler

    @wraps(handler)
    async def async_wrapper(request: Request, exc: RequestValidationError) -> Any:
        _record_validation_errors(request, exc)
        return await handler(request, exc)

    @wraps(handler)
    def sync_wrapper(request: Request, exc: RequestValidationError) -> Any:
        _record_validation_errors(request, exc)
        return handler(request, exc)

    wrapped = async_wrapper if inspect.iscoroutinefunction(handler) else sync_wrapper
    setattr(wrapped, _WRAPPED, True)
    return wrapped


def install_validation_error_observer(app: FastAPI) -> None:
    """Wrap ``RequestValidationError`` handling on *app* to record ``loc`` and ``type``."""
    current = app.exception_handlers.get(
        RequestValidationError, request_validation_exception_handler
    )
    app.exception_handlers[RequestValidationError] = _wrap_validation_handler(current)

    original_add = app.add_exception_handler
    if not getattr(original_add, _WRAPPED, False):

        @wraps(original_add)
        def add_exception_handler(
            exc_class_or_status_code: int | type[Exception],
            handler: Callable[..., Any],
        ) -> None:
            if (
                isinstance(exc_class_or_status_code, type)
                and issubclass(exc_class_or_status_code, RequestValidationError)
            ):
                handler = _wrap_validation_handler(handler)
            original_add(exc_class_or_status_code, handler)

        setattr(add_exception_handler, _WRAPPED, True)
        app.add_exception_handler = add_exception_handler  # type: ignore[method-assign]


__all__ = ["install_validation_error_observer"]
