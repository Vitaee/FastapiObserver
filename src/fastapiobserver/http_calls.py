"""Count explicitly instrumented HTTP clients without depending on sampled spans."""

from __future__ import annotations

import inspect
import logging
import sys
import threading
from collections.abc import AsyncIterator, Callable, Iterator
from contextvars import ContextVar
from dataclasses import dataclass
from functools import cache, wraps
from types import ModuleType
from typing import Any
from urllib.parse import urlsplit

from .core.recording import CallHandle
from .request_context import RequestState, get_request_state
from .utils import collapse_dynamic_segments, normalize_path

_LOGGER = logging.getLogger("fastapiobserver.http_calls")
_ACTIVE_CLIENT: ContextVar[object | None] = ContextVar("observed_http_client", default=None)
_COUNTER = "_fastapiobserver_http_calls"
_GLOBAL_SENDS: dict[type[Any], tuple[str, Callable[..., Any], Callable[..., Any]]] = {}
_MAX_WARNED_TIMEOUTS = 256
_TIMEOUT_LOCK = threading.Lock()
_warned_timeouts: dict[tuple[str, str], None] = {}


def _error_category(error: BaseException, library: ModuleType) -> str:
    exceptions = getattr(library, "exceptions", library)
    # Requests converts a streamed urllib3 read timeout into ConnectionError.
    # Keep the timeout category without depending on exception text.
    urllib3 = sys.modules.get("urllib3.exceptions")
    if library.__name__ == "requests" and urllib3 is not None and error.args:
        if isinstance(error.args[0], urllib3.ReadTimeoutError):
            return "read_timeout"
    for name, category in (
        ("ConnectTimeout", "connect_timeout"),
        ("ReadTimeout", "read_timeout"),
        ("WriteTimeout", "write_timeout"),
        ("PoolTimeout", "pool_timeout"),
        ("TimeoutException", "timeout"),
        ("Timeout", "timeout"),
        ("NetworkError", "network_error"),
        ("ProtocolError", "network_error"),
        ("ConnectionError", "network_error"),
    ):
        cls = getattr(exceptions, name, None)
        if cls is not None and isinstance(error, cls):
            return category
    return "request_error" if isinstance(error, Exception) else "cancelled"


@dataclass(slots=True)
class _Call:
    recorder: CallHandle
    library: ModuleType
    target: str | None = None
    status: int = 0

    def finish(self, error: BaseException | None = None) -> None:
        category = (
            _error_category(error, self.library)
            if error is not None
            else ("http_5xx" if self.status >= 500 else "http_4xx" if self.status >= 400 else None)
        )
        self.recorder.finish(
            error=category,
            statement=self.target,
            relationship=self.target[:200] if self.target else None,
        )


def _http_target(request: Any) -> tuple[str, str | None]:
    try:
        method = (getattr(request, "method", None) or "GET").upper()
        url = getattr(request, "url", None)
        if isinstance(url, str):
            parts = urlsplit(url)
            host = (parts.hostname or "").lower()
            port = parts.port
            path = parts.path or "/"
        else:
            host = (getattr(url, "host", None) or "").lower()
            port = getattr(url, "port", None)
            path = getattr(url, "path", None) or "/"
        if not host:
            return "", None
        netloc = f"{host}:{port}" if port not in (None, 80, 443) else host
        collapsed = collapse_dynamic_segments(normalize_path(path))
        return netloc, f"{method} {netloc}{collapsed}"
    except Exception:
        return "", None


def _unlimited_httpx_phases(timeout: Any) -> tuple[str, ...]:
    if timeout is None:
        return ("connect", "read", "write", "pool")
    phases = timeout if isinstance(timeout, dict) else getattr(timeout, "as_dict", lambda: {})()
    return tuple(name for name, val in phases.items() if val is None)


def _unlimited_requests_phases(timeout: Any) -> tuple[str, ...]:
    if timeout is None:
        return ("connect", "read")
    if isinstance(timeout, tuple):
        connect, read = (timeout + (None, None))[:2]
        return tuple(
            name for name, val in (("connect", connect), ("read", read)) if val is None
        )
    return ()


def _warn_missing_timeout(
    library_name: str, host: str, unlimited: tuple[str, ...]
) -> None:
    if not unlimited:
        return
    key = (library_name, host)
    with _TIMEOUT_LOCK:
        if key in _warned_timeouts:
            return
        if len(_warned_timeouts) >= _MAX_WARNED_TIMEOUTS:
            del _warned_timeouts[next(iter(_warned_timeouts))]
        _warned_timeouts[key] = None
    _LOGGER.warning(
        "http.timeout_missing",
        extra={
            "event": {
                "library": library_name,
                "host": host or None,
                "unlimited": list(unlimited),
            },
        },
    )


def is_counted_http_span(state: RequestState, span_id: int | None) -> bool:
    """Avoid counting both a client call and its OpenTelemetry span, in either wrap order."""
    counted = span_id in state.http_span_ids
    state.http_span_ids.discard(span_id)
    return counted


def mark_native_http_span(state: RequestState, span_id: int) -> None:
    """Capture native ownership at span start, before its context can change."""
    if _ACTIVE_CLIENT.get() is not None:
        state.mark_http_span(span_id)


def _mark_current_span(state: RequestState) -> None:
    trace = sys.modules.get("opentelemetry.trace")
    if trace is not None:
        span = trace.get_current_span()
        if span.is_recording() and getattr(span, "kind", None) == trace.SpanKind.CLIENT:
            state.mark_http_span(span.get_span_context().span_id)


@cache
def _stream_types(library: ModuleType) -> tuple[type[Any], type[Any]]:
    # Created once per installed library, never once per response. Keep client
    # dependencies optional while satisfying each client's stream type checks.
    class SyncStream(library.SyncByteStream):  # type: ignore[misc, name-defined]
        def __init__(self, stream: Any, call: _Call) -> None:
            self.stream, self.call = stream, call

        def __iter__(self) -> Iterator[bytes]:
            try:
                yield from self.stream
            except GeneratorExit:
                raise
            except BaseException as exc:
                self.call.finish(exc)
                raise

        def close(self) -> None:
            try:
                self.stream.close()
            except BaseException as exc:
                self.call.finish(exc)
                raise
            self.call.finish()

    class AsyncStream(library.AsyncByteStream):  # type: ignore[misc, name-defined]
        def __init__(self, stream: Any, call: _Call) -> None:
            self.stream, self.call = stream, call

        async def __aiter__(self) -> AsyncIterator[bytes]:
            try:
                async for chunk in self.stream:
                    yield chunk
            except GeneratorExit:
                raise
            except BaseException as exc:
                self.call.finish(exc)
                raise

        async def aclose(self) -> None:
            try:
                await self.stream.aclose()
            except BaseException as exc:
                self.call.finish(exc)
                raise
            self.call.finish()

    return SyncStream, AsyncStream


def _watch_response(response: Any, call: _Call, *, asynchronous: bool) -> None:
    call.status = response.status_code
    if call.library.__name__ != "requests":
        if response.is_closed:
            call.finish()
        else:
            stream_type = _stream_types(call.library)[int(asynchronous)]
            response.stream = stream_type(response.stream, call)
        return

    # Requests consumes non-streaming responses before Session.send returns.
    # Streamed responses finish when iter_content is exhausted or close is called.
    if response._content_consumed:
        call.finish()
        return
    iterate, close = response.iter_content, response.close

    @wraps(iterate)
    def iter_content(*args: Any, **kwargs: Any) -> Iterator[bytes]:
        try:
            yield from iterate(*args, **kwargs)
        except GeneratorExit:
            raise
        except BaseException as exc:
            call.finish(exc)
            raise
        call.finish()

    @wraps(close)
    def close_response() -> None:
        try:
            close()
        except BaseException as exc:
            call.finish(exc)
            raise
        call.finish()

    response.iter_content, response.close = iter_content, close_response


def _wrap_send(
    original: Callable[..., Any],
    library: ModuleType,
    forward: Callable[[Any], None],
    client: Any = None,
) -> Callable[..., Any]:
    def begin(args: tuple[Any, ...], kwargs: dict[str, Any]) -> tuple[object, _Call | None]:
        owner = client if client is not None else args[0]
        request = kwargs["request"] if "request" in kwargs else args[int(client is None)]
        forward(request.headers)
        if _ACTIVE_CLIENT.get() is owner:
            return owner, None
        host, target = _http_target(request)
        if library.__name__ == "requests":
            unlimited = _unlimited_requests_phases(
                kwargs.get("timeout", getattr(owner, "timeout", None))
            )
        else:
            extensions = getattr(request, "extensions", None)
            ext_timeout = extensions.get("timeout") if isinstance(extensions, dict) else None
            unlimited = _unlimited_httpx_phases(
                ext_timeout if ext_timeout is not None else getattr(owner, "timeout", None)
            )
        _warn_missing_timeout(library.__name__, host, unlimited)
        state = get_request_state()
        # Requests follows redirects through Session.send again. Also covers a
        # client instrumented both individually and globally, without losing calls
        # made by other clients in a request hook.
        if state is None:
            return owner, None
        state.on_http_start(target)
        _mark_current_span(state)
        return owner, _Call(
            CallHandle(state, "http", source=library.__name__),
            library,
            target=target,
        )

    @wraps(original)
    def send(*args: Any, **kwargs: Any) -> Any:
        owner, call = begin(args, kwargs)
        if call is None:
            return original(*args, **kwargs)
        token = _ACTIVE_CLIENT.set(owner)
        try:
            response = original(*args, **kwargs)
            _watch_response(response, call, asynchronous=False)
            return response
        except BaseException as exc:
            call.finish(exc)
            raise
        finally:
            _ACTIVE_CLIENT.reset(token)

    @wraps(original)
    async def async_send(*args: Any, **kwargs: Any) -> Any:
        owner, call = begin(args, kwargs)
        if call is None:
            return await original(*args, **kwargs)
        token = _ACTIVE_CLIENT.set(owner)
        try:
            response = await original(*args, **kwargs)
            _watch_response(response, call, asynchronous=True)
            return response
        except BaseException as exc:
            call.finish(exc)
            raise
        finally:
            _ACTIVE_CLIENT.reset(token)

    wrapper = async_send if inspect.iscoroutinefunction(original) else send
    setattr(wrapper, _COUNTER, True)
    return wrapper


def count_client_calls(client: Any, library: ModuleType, forward: Callable[[Any], None]) -> None:
    if not getattr(vars(client).get("send"), _COUNTER, False):
        base_host = (getattr(getattr(client, "base_url", None), "host", None) or "").lower()
        _warn_missing_timeout(
            library.__name__,
            base_host,
            _unlimited_httpx_phases(getattr(client, "timeout", ...)),
        )
        client.send = _wrap_send(client.send, library, forward, client)


def count_global_calls(
    client_type: type[Any], library: ModuleType, forward: Callable[[Any], None]
) -> None:
    if client_type not in _GLOBAL_SENDS:
        original = client_type.send
        wrapper = _wrap_send(original, library, forward)
        _GLOBAL_SENDS[client_type] = library.__name__, original, wrapper
        client_type.send = wrapper


def uncount_global_calls(*libraries: str) -> None:
    with _TIMEOUT_LOCK:
        for key in [k for k in _warned_timeouts if k[0] in libraries]:
            del _warned_timeouts[key]
    for client_type, (name, original, wrapper) in list(_GLOBAL_SENDS.items()):
        if name in libraries:
            if client_type.send is wrapper:
                client_type.send = original
            del _GLOBAL_SENDS[client_type]
