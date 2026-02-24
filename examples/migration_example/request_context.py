"""
Context management for request ID tracing across async bounds.
"""
from contextvars import ContextVar
import uuid

_request_id_ctx_var: ContextVar[str] = ContextVar("request_id", default=None)
_user_context_var: ContextVar[dict] = ContextVar("user_context", default={})

def generate_request_id() -> str:
    return str(uuid.uuid4())

def get_request_id() -> str | None:
    return _request_id_ctx_var.get()

def set_request_id(request_id: str):
    return _request_id_ctx_var.set(request_id)

def get_user_context() -> dict:
    return _user_context_var.get()

def set_user_context(context: dict):
    return _user_context_var.set(context)
