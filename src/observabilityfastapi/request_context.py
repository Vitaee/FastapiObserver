from __future__ import annotations

from contextvars import ContextVar
from typing import Any

_request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
_trace_id_var: ContextVar[str | None] = ContextVar("trace_id", default=None)
_span_id_var: ContextVar[str | None] = ContextVar("span_id", default=None)
_user_context_var: ContextVar[dict[str, Any] | None] = ContextVar(
    "user_context", default=None
)


def set_request_id(value: str | None) -> None:
    _request_id_var.set(value)


def get_request_id() -> str | None:
    return _request_id_var.get()


def clear_request_id() -> None:
    _request_id_var.set(None)


def set_trace_id(value: str | None) -> None:
    _trace_id_var.set(value)


def get_trace_id() -> str | None:
    return _trace_id_var.get()


def clear_trace_id() -> None:
    _trace_id_var.set(None)


def set_span_id(value: str | None) -> None:
    _span_id_var.set(value)


def get_span_id() -> str | None:
    return _span_id_var.get()


def clear_span_id() -> None:
    _span_id_var.set(None)


def set_user_context(value: dict[str, Any] | None) -> None:
    _user_context_var.set(value)


def get_user_context() -> dict[str, Any] | None:
    return _user_context_var.get()


def clear_user_context() -> None:
    _user_context_var.set(None)
