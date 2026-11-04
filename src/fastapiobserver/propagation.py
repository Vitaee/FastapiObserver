"""Outbound HTTP instrumentation and context propagation.

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

    instrument_httpx_client()  # all httpx calls get traceparent and the request ID

``install_observability()`` sets the request ID header to forward from
``OBS_REQUEST_ID_HEADER``.
"""

from __future__ import annotations

import importlib.util
import logging
from collections.abc import Mapping
from typing import Any

from .http_calls import count_client_calls, count_global_calls, uncount_global_calls
from .request_context import get_request_id
from .utils import lazy_import

_LOGGER = logging.getLogger("fastapiobserver.propagation")
# ponytail: one header per process, the last install_observability() wins; key it
# per app if one process ever runs apps with different OBS_REQUEST_ID_HEADER values.
_request_id_header = "x-request-id"


# ---------------------------------------------------------------------------
# Request ID forwarding
# ---------------------------------------------------------------------------


def _request_id_to_forward(carrier: Any) -> str | None:
    """The current request ID, unless *carrier* already has a request ID header."""
    request_id = get_request_id()
    if request_id is None:
        return None
    if isinstance(carrier, Mapping) and any(
        str(key).lower() == _request_id_header for key in carrier
    ):
        return None
    return request_id


def _forward_request_id(headers: Any) -> None:
    request_id = _request_id_to_forward(headers)
    if request_id is not None:
        headers[_request_id_header] = request_id


def _optional_instrumentor(module: str, name: str) -> Any:
    try:
        return getattr(importlib.import_module(module), name, None)
    except ImportError:
        return None


def install_request_id_propagation(header: str) -> None:
    """Send the current request ID as *header* on outbound calls.

    Adds a propagator next to OpenTelemetry's global one, so every client
    OpenTelemetry instruments (httpx, requests, ...) forwards the request ID
    alongside ``traceparent``. Without OpenTelemetry installed, only
    ``inject_trace_headers()`` forwards it.
    """
    global _request_id_header
    _request_id_header = header
    try:
        from opentelemetry import propagate
        from opentelemetry.context import Context
        from opentelemetry.propagators.composite import CompositePropagator
        from opentelemetry.propagators.textmap import (
            Getter,
            Setter,
            TextMapPropagator,
            default_getter,
            default_setter,
        )
    except ImportError:
        return

    current = propagate.get_global_textmap()
    if header in current.fields:
        return

    class RequestIdPropagator(TextMapPropagator):
        def inject(
            self,
            carrier: Any,
            context: Context | None = None,
            setter: Setter[Any] = default_setter,
        ) -> None:
            request_id = _request_id_to_forward(carrier)
            if request_id is not None:
                setter.set(carrier, _request_id_header, request_id)

        def extract(
            self,
            carrier: Any,
            context: Context | None = None,
            getter: Getter[Any] = default_getter,
        ) -> Context:
            # The middleware reads incoming request IDs, and only from trusted proxies.
            return context if context is not None else Context()

        @property
        def fields(self) -> set[str]:
            return {_request_id_header}

    propagate.set_global_textmap(CompositePropagator([current, RequestIdPropagator()]))


# ---------------------------------------------------------------------------
# Manual injection — zero dependencies beyond OTel API
# ---------------------------------------------------------------------------


def inject_trace_headers(headers: dict[str, str] | None = None) -> dict[str, str]:
    """Inject the current request ID and OTel trace context into *headers*.

    Returns the (mutated) *headers* dict with the request ID header, and
    ``traceparent`` and ``tracestate`` entries when a valid span exists.
    A request ID already in *headers* is kept. If OTel is not installed,
    only the request ID is added.

    Parameters
    ----------
    headers:
        Mutable dict to inject into.  A new dict is created if ``None``.
    """
    if headers is None:
        headers = {}
    request_id = _request_id_to_forward(headers)
    if request_id is not None:
        headers[_request_id_header] = request_id
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
    """Count ``httpx``/``httpx2`` calls and forward request IDs, with optional tracing.

    Pass a client to instrument only that instance, or omit it for all installed
    HTTPX clients. Counts, durations and failures need no OpenTelemetry extra.
    Installing ``[otel-httpx]`` also enables the client's OpenTelemetry tracing.
    """
    matched = False
    for name, instrumentor_name in (
        ("httpx", "HTTPXClientInstrumentor"), ("httpx2", "HTTPX2ClientInstrumentor"),
    ):
        if importlib.util.find_spec(name) is None:
            continue
        library = importlib.import_module(name)
        if client is not None and not isinstance(client, (library.Client, library.AsyncClient)):
            continue
        matched = True
        instrumentor = _optional_instrumentor(
            "opentelemetry.instrumentation.httpx", instrumentor_name
        )
        if client is None:
            if instrumentor is not None and not instrumentor().is_instrumented_by_opentelemetry:
                instrumentor().instrument()
            for cls in (library.Client, library.AsyncClient):
                count_global_calls(cls, library, _forward_request_id)
        else:
            if instrumentor is not None and not getattr(
                client, "_is_instrumented_by_opentelemetry", False
            ):
                instrumentor.instrument_client(client)
            count_client_calls(client, library, _forward_request_id)
    if not matched:
        raise RuntimeError(
            "instrument_httpx_client() needs an installed `httpx` or `httpx2` client."
        )


def uninstrument_httpx_client() -> None:
    """Reverse global ``httpx`` instrumentation."""
    uncount_global_calls("httpx", "httpx2")
    for name in ("HTTPXClientInstrumentor", "HTTPX2ClientInstrumentor"):
        instrumentor = _optional_instrumentor("opentelemetry.instrumentation.httpx", name)
        if instrumentor is not None and instrumentor().is_instrumented_by_opentelemetry:
            instrumentor().uninstrument()


# ---------------------------------------------------------------------------
# requests auto-instrumentation
# ---------------------------------------------------------------------------


def instrument_requests_session(session: Any | None = None) -> None:
    """Instrument ``requests`` for automatic outbound trace propagation.

    .. note::

       Requests instrumentation is global. Per-session instrumentation is not
       supported; passing *session* raises ``TypeError``.

    Counts and request IDs need only ``requests``. The ``[otel-requests]`` extra
    adds OpenTelemetry tracing.
    """
    if session is not None:
        raise TypeError(
            "Per-session instrumentation is not supported by "
            "RequestsInstrumentor. Call without arguments to "
            "instrument all requests globally."
        )
    library = lazy_import("requests", package_hint="requests")
    instrumentor = _optional_instrumentor(
        "opentelemetry.instrumentation.requests", "RequestsInstrumentor"
    )
    if instrumentor is not None and not instrumentor().is_instrumented_by_opentelemetry:
        instrumentor().instrument()
    count_global_calls(library.Session, library, _forward_request_id)


def uninstrument_requests_session() -> None:
    """Reverse global ``requests`` instrumentation."""
    uncount_global_calls("requests")
    instrumentor = _optional_instrumentor(
        "opentelemetry.instrumentation.requests", "RequestsInstrumentor"
    )
    if instrumentor is not None and instrumentor().is_instrumented_by_opentelemetry:
        instrumentor().uninstrument()


__all__ = [
    "inject_trace_headers",
    "instrument_httpx_client",
    "instrument_requests_session",
    "uninstrument_httpx_client",
    "uninstrument_requests_session",
]
