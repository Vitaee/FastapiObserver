"""Outbound trace context propagation helpers (WS5).

Provides convenience functions for injecting trace context into outbound
HTTP requests, plus optional auto-instrumentation of ``httpx`` and
``requests`` clients.

Usage::

    from fastapiobserver.propagation import inject_trace_headers

    headers = inject_trace_headers({})
    async with httpx.AsyncClient() as client:
        await client.get("http://other-service/api", headers=headers)

Or for automatic instrumentation::

    from fastapiobserver.propagation import instrument_httpx_client

    instrument_httpx_client()  # all httpx calls get traceparent automatically
"""

from __future__ import annotations

import logging
from typing import Any

from .utils import lazy_import

_LOGGER = logging.getLogger("fastapiobserver.propagation")


# ---------------------------------------------------------------------------
# Manual injection — zero dependencies beyond OTel API
# ---------------------------------------------------------------------------


def inject_trace_headers(headers: dict[str, str] | None = None) -> dict[str, str]:
    """Inject the current OTel trace context into *headers*.

    Returns the (mutated) *headers* dict with ``traceparent`` and
    ``tracestate`` entries added when a valid span exists.  If OTel is
    not installed, *headers* is returned unchanged.

    Parameters
    ----------
    headers:
        Mutable dict to inject into.  A new dict is created if ``None``.
    """
    if headers is None:
        headers = {}
    try:
        from opentelemetry.propagate import inject

        inject(headers)
    except ImportError:
        _LOGGER.debug(
            "propagation.inject.otel_unavailable",
            extra={"_skip_enrichers": True},
        )
    except Exception:
        _LOGGER.debug(
            "propagation.inject.failed",
            exc_info=True,
            extra={"_skip_enrichers": True},
        )
    return headers


# ---------------------------------------------------------------------------
# httpx auto-instrumentation
# ---------------------------------------------------------------------------


def instrument_httpx_client(client: Any | None = None) -> None:
    """Instrument ``httpx`` for automatic outbound trace propagation.

    When *client* is provided, only that specific client instance is
    instrumented.  When ``None``, **all** ``httpx`` clients are
    instrumented globally.

    Requires ``pip install fastapi-observer[otel-httpx]``.
    """
    try:
        instrumentor_module = lazy_import(
            "opentelemetry.instrumentation.httpx",
            package_hint="fastapi-observer[otel-httpx]",
        )
        instrumentor_cls = getattr(instrumentor_module, "HTTPXClientInstrumentor")

        if client is not None:
            instrumentor_cls.instrument_client(client)
        else:
            instrumentor_cls().instrument()
    except RuntimeError as exc:
        raise RuntimeError(
            "httpx instrumentation requires "
            "`pip install fastapi-observer[otel-httpx]`"
        ) from exc


def uninstrument_httpx_client() -> None:
    """Reverse global ``httpx`` instrumentation."""
    try:
        instrumentor_module = lazy_import("opentelemetry.instrumentation.httpx")
        instrumentor_cls = getattr(instrumentor_module, "HTTPXClientInstrumentor")

        instrumentor_cls().uninstrument()
    except ModuleNotFoundError:
        pass


# ---------------------------------------------------------------------------
# requests auto-instrumentation
# ---------------------------------------------------------------------------


def instrument_requests_session(session: Any | None = None) -> None:
    """Instrument ``requests`` for automatic outbound trace propagation.

    .. note::

       The OTel ``RequestsInstrumentor`` instruments globally at the
       ``urllib3`` transport layer.  Per-session instrumentation is not
       supported; passing *session* will raise ``TypeError``.

    Requires ``pip install fastapi-observer[otel-requests]``.
    """
    if session is not None:
        raise TypeError(
            "Per-session instrumentation is not supported by "
            "RequestsInstrumentor. Call without arguments to "
            "instrument all requests globally."
        )
    try:
        instrumentor_module = lazy_import(
            "opentelemetry.instrumentation.requests",
            package_hint="fastapi-observer[otel-requests]",
        )
        instrumentor_cls = getattr(instrumentor_module, "RequestsInstrumentor")

        instrumentor_cls().instrument()
    except RuntimeError as exc:
        raise RuntimeError(
            "requests instrumentation requires "
            "`pip install fastapi-observer[otel-requests]`"
        ) from exc


def uninstrument_requests_session() -> None:
    """Reverse global ``requests`` instrumentation."""
    try:
        instrumentor_module = lazy_import("opentelemetry.instrumentation.requests")
        instrumentor_cls = getattr(instrumentor_module, "RequestsInstrumentor")

        instrumentor_cls().uninstrument()
    except ModuleNotFoundError:
        pass


__all__ = [
    "inject_trace_headers",
    "instrument_httpx_client",
    "instrument_requests_session",
    "uninstrument_httpx_client",
    "uninstrument_requests_session",
]
