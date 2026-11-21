from __future__ import annotations

import logging
import time
from typing import Any

from anyio import get_cancelled_exc_class
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from starlette.websockets import WebSocketDisconnect

from ..config import ObservabilitySettings
from ..core.recording import CallKind
from ..diagnostics import report_diagnostic
from ..logging.filters import RequestIdFilter, TraceContextFilter
from ..logging.held import LogSamplingFilter, keep_request_logs, will_write_request_log
from ..gc_pauses import GC_PAUSE_SECONDS, gc_seconds
from ..loop_watchdog import last_loop_block_window
from ..metrics import MetricsBackend, NoopMetricsBackend
from ..pools import check_pools
from ..plugins import _METRIC_HOOKS
from ..request_context import _N_PLUS_ONE_MIN_REPEATS, UNMATCHED_ROUTE, RequestState
from ..security import (
    SecurityPolicy,
    TrustedProxyPolicy,
    is_body_capturable,
)
from .body_capture import _BodyCapture
from .context import _RequestContextManager
from .events import _RequestEventBuilder, _classify_error, _extract_route_template
from .headers import _get_header, _upsert_header
from .ip import _IpResolver
from .metrics import _MetricsRecorder

_MAX_REPORTED_STATEMENTS = 1024
# Metric labels take the method only from this set, as OpenTelemetry does, so a
# client can't add a time series per made-up method.
_HTTP_METHODS = frozenset(
    {"GET", "HEAD", "POST", "PUT", "DELETE", "CONNECT", "OPTIONS", "TRACE", "PATCH"}
)
_CONTEXT_FILTERS = (RequestIdFilter(), TraceContextFilter(), LogSamplingFilter())


def _ws_frame_bytes(message: Message) -> int:
    data = message.get("bytes")
    if isinstance(data, (bytes, bytearray)):
        return len(data)
    text = message.get("text")
    return len(text.encode("utf-8")) if isinstance(text, str) else 0


def _tag_pauses(
    request_state: RequestState, start: float, end: float, *, include_gc: bool = True
) -> None:
    """Tag a request that the event loop or the garbage collector stalled while it ran."""
    block_start, block_end = last_loop_block_window()
    if block_end > start and block_start < end:
        request_state.record_diagnostic_fact("event_loop.blocked")
    if include_gc:
        gc_pause = gc_seconds() - request_state.gc_seconds_at_start
        if gc_pause >= GC_PAUSE_SECONDS:
            request_state.record_gc_pause(gc_pause)


def _attach_state_fields(
    event: dict[str, Any], route_template: str, request_state: RequestState
) -> None:
    if route_template != UNMATCHED_ROUTE:
        event["http.route"] = route_template
    if request_state.after_disconnect_calls:
        event["after_disconnect_calls"] = dict(request_state.after_disconnect_calls)
    if not request_state.closed:
        request_state.check_held_connection()
        if request_state.validation_errors:
            event["validation_errors"] = list(request_state.validation_errors)
        if request_state.observed_diagnostics:
            event["diagnostics"] = sorted(request_state.observed_diagnostics)
        if request_state.gc_pause_seconds is not None:
            event["gc_pause_ms"] = round(request_state.gc_pause_seconds * 1000)
        event.update(request_state.summarize_calls())


class RequestLoggingMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        settings: ObservabilitySettings,
        security_policy: SecurityPolicy | None = None,
        trusted_proxy_policy: TrustedProxyPolicy | None = None,
        metrics_backend: MetricsBackend | None = None,
        configure_logging: bool = True,
    ) -> None:
        self.app = app
        self.settings = settings
        self.configure_logging = configure_logging
        self.security_policy = security_policy or SecurityPolicy()
        self.trusted_proxy_policy = trusted_proxy_policy or TrustedProxyPolicy()
        self.metrics_backend = metrics_backend or NoopMetricsBackend()
        self.logger = logging.getLogger("fastapiobserver.middleware")
        # Capture context before application filters and queues see diagnostics.
        # Filters also work with factories that already define these fields.
        for context_filter in reversed(_CONTEXT_FILTERS):
            if context_filter not in self.logger.filters:
                self.logger.filters.insert(0, context_filter)
        self.ip_resolver = _IpResolver(self.trusted_proxy_policy)
        self.context_manager = _RequestContextManager(self.settings)
        self.event_builder = _RequestEventBuilder(self.security_policy)
        self.metrics_recorder = _MetricsRecorder(
            settings=self.settings,
            metrics_backend=self.metrics_backend,
            logger=self.logger,
        )
        self._reported_statements: dict[tuple[str, ...], None] = {}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket":
            await self._handle_websocket(scope, receive, send)
            return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start = time.perf_counter()
        raw_headers = scope.get("headers", [])
        client_ip, trusted_source = self.ip_resolver.resolve(scope, raw_headers)
        request_id, request_state = self.context_manager.initialize(
            scope, raw_headers, trusted_source
        )
        request_state.observer_logging = self.configure_logging

        path = scope.get("path", "")
        method = scope.get("method", "UNKNOWN")
        status_code = 500
        captured_error: Exception | None = None
        request_body_enabled = self.security_policy.log_request_body and is_body_capturable(
            _get_header(raw_headers, b"content-type"),
            self.security_policy,
        )
        request_body_capture = _BodyCapture(
            enabled=request_body_enabled,
            max_length=self.security_policy.max_body_length,
        )
        response_body_capture = _BodyCapture(
            enabled=self.security_policy.log_response_body,
            max_length=self.security_policy.max_body_length,
        )
        had_error = False
        cancelled = False
        response_started: float | None = None
        first_body: float | None = None
        response_finished: float | None = None
        response_trailers = False
        response_bytes = 0
        disconnected_at: float | None = None

        async def receive_wrapper() -> Message:
            nonlocal disconnected_at
            message = await receive()
            if (message["type"] == "http.disconnect" and disconnected_at is None
                    and response_finished is None):
                disconnected_at = time.perf_counter()
                request_state.disconnected = True
            request_body_capture.capture_from_message(message, "http.request")
            return message

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code, response_started, first_body, response_finished
            nonlocal response_trailers, response_bytes
            if message["type"] == "http.response.start":
                status_code = int(message.get("status", 500))
                response_trailers = bool(message.get("trailers", False))
                resp_headers = message.get("headers", [])
                if self.security_policy.log_response_body:
                    response_content_type = _get_header(resp_headers, b"content-type")
                    response_body_capture.set_enabled(
                        is_body_capturable(response_content_type, self.security_policy)
                    )
                updated_headers = _upsert_header(
                    resp_headers,
                    self.settings.response_request_id_header,
                    request_id,
                )
                message = dict(message)
                message["headers"] = updated_headers

            response_body_capture.capture_from_message(message, "http.response.body")
            if (message["type"] == "http.response.body" and not message.get("more_body", False)
                    and request_state.span is not None):
                # ASGI tracing may end its server span inside send(final_body).
                self._report_repeated_statement(method, "", request_state, warn=False)
            await send(message)
            if message["type"] == "http.response.start":
                response_started = time.perf_counter()
            if message["type"] == "http.response.body":
                response_bytes += len(message.get("body", b""))
            first_chunk = (
                first_body is None and message["type"] == "http.response.body"
                and bool(message.get("body"))
            )
            finished = (
                message["type"] == "http.response.body"
                and not message.get("more_body", False)
                and not response_trailers
            ) or (
                message["type"] == "http.response.trailers"
                and not message.get("more_trailers", False)
            )
            if first_chunk or (finished and response_finished is None):
                sent_at = time.perf_counter()
                if first_chunk:
                    first_body = sent_at
            if finished and response_finished is None:
                response_finished = sent_at
                self._observe_response(
                    scope, method, status_code, response_finished - start, request_state
                )

        try:
            check_pools()
            await self.app(
                scope,
                receive_wrapper,
                send_wrapper,
            )
        except Exception as exc:
            had_error = True
            captured_error = exc
            if response_started is None:
                status_code = 500
            raise
        except get_cancelled_exc_class():
            # The client left, the server is shutting down or a timeout fired.
            # Not a server error: without a response, use nginx's 499.
            cancelled = True
            if response_started is None:
                status_code = 499
            raise
        finally:
            try:
                if disconnected_at is not None and response_started is None and not had_error:
                    status_code = 499
                self._finalize_request(
                    scope=scope,
                    start=start,
                    method=method,
                    path=path,
                    client_ip=client_ip,
                    status_code=status_code,
                    request_body_capture=request_body_capture,
                    response_body_capture=response_body_capture,
                    captured_error=captured_error,
                    had_error=had_error,
                    cancelled=cancelled,
                    response_started=response_started,
                    first_body=first_body,
                    response_finished=response_finished,
                    response_bytes=response_bytes,
                    disconnected_at=disconnected_at,
                    request_state=request_state,
                )
            finally:
                self.context_manager.cleanup(request_state)

    def _finalize_request(
        self,
        scope: Scope,
        start: float,
        method: str,
        path: str,
        client_ip: str | None,
        status_code: int,
        request_body_capture: _BodyCapture,
        response_body_capture: _BodyCapture,
        captured_error: Exception | None,
        had_error: bool,
        cancelled: bool,
        response_started: float | None,
        first_body: float | None,
        response_finished: float | None,
        response_bytes: int,
        disconnected_at: float | None,
        request_state: RequestState,
    ) -> None:
        app_finished = time.perf_counter()
        _tag_pauses(request_state, start, app_finished)
        app_duration_seconds = max(0.0, app_finished - start)
        duration_seconds = max(
            0.0, (response_finished if response_finished is not None else app_finished) - start
        )
        if had_error:
            level, message = logging.ERROR, "request.failed"
        elif cancelled:
            level, message = logging.WARNING, "request.cancelled"
        elif disconnected_at is not None:
            level, message = logging.WARNING, "request.client_disconnected"
        elif status_code >= 500:
            level, message = logging.ERROR, "request.server_error"
        elif status_code >= 400:
            level, message = logging.WARNING, "request.client_error"
        else:
            level, message = logging.INFO, "request.completed"

        route_template = _extract_route_template(scope, request_state)
        failed = level >= logging.ERROR
        if failed or app_duration_seconds * 1000 >= self.settings.log_slow_request_ms:
            keep_request_logs(failed=failed)
        # Building and redacting the event is the costliest step; skip it when
        # the record would be dropped by the log level or sampling policy anyway.
        if self.logger.isEnabledFor(level) and (
            self.logger.handlers or will_write_request_log(level, request_state)
        ):
            safe_event = self.event_builder.build(
                method=method,
                path=path,
                status_code=status_code,
                duration_seconds=duration_seconds,
                client_ip=client_ip,
                request_body=request_body_capture.value,
                response_body=response_body_capture.value,
                error_type=_classify_error(status_code, captured_error),
                exception=captured_error,
            )
            safe_event["app_duration_ms"] = round(app_duration_seconds * 1000, 3)
            safe_event["response_start_ms"] = (
                round(max(0.0, response_started - start) * 1000, 3)
                if response_started is not None else None
            )
            if first_body is None:
                safe_event["first_body_ms"] = safe_event["stream_duration_ms"] = None
            elif first_body == response_finished:
                # A single successful body send already has its elapsed time.
                safe_event["first_body_ms"] = safe_event["duration_ms"]
                safe_event["stream_duration_ms"] = 0.0
            else:
                safe_event["first_body_ms"] = round(max(0.0, first_body - start) * 1000, 3)
                safe_event["stream_duration_ms"] = (
                    round(max(0.0, response_finished - first_body) * 1000, 3)
                    if response_finished is not None else None
                )
            safe_event["response_bytes"] = response_bytes
            safe_event["response_completed"] = response_finished is not None
            safe_event["cancelled"] = cancelled
            safe_event["client_disconnected"] = disconnected_at is not None
            if disconnected_at is not None:
                safe_event["after_disconnect_ms"] = round(
                    max(0.0, app_finished - disconnected_at) * 1000, 3
                )
            _attach_state_fields(safe_event, route_template, request_state)
            self.logger.log(
                level,
                message,
                exc_info=captured_error if had_error else None,
                extra={"event": safe_event},
            )

        metric_method = method if method in _HTTP_METHODS else "_OTHER"
        self._emit_request_diagnostics(metric_method, route_template, request_state)

        if response_finished is None:
            self._observe_response(scope, method, status_code, duration_seconds, request_state)

    async def _handle_websocket(self, scope: Scope, receive: Receive, send: Send) -> None:
        start = time.perf_counter()
        raw_headers = scope.get("headers", [])
        client_ip, trusted_source = self.ip_resolver.resolve(scope, raw_headers)
        _request_id, request_state = self.context_manager.initialize(
            scope, raw_headers, trusted_source
        )
        request_state.observer_logging = self.configure_logging
        # OBS_LOG_SAMPLE_RATE is for requests. A session can stay open for hours,
        # and holding its lines until it closes would then drop them.
        request_state.sampled = True
        accepted = False
        close_code: int | None = None
        messages_received = messages_sent = bytes_received = bytes_sent = 0
        captured_error: Exception | None = None

        async def ws_receive() -> Message:
            nonlocal close_code, messages_received, bytes_received
            message = await receive()
            msg_type = message["type"]
            if msg_type == "websocket.receive":
                messages_received += 1
                bytes_received += _ws_frame_bytes(message)
                request_state.forget_unrepeated_calls()
            elif msg_type == "websocket.disconnect":
                close_code = int(message.get("code") or 1000)
                request_state.disconnected = True
            return message

        async def ws_send(message: Message) -> None:
            nonlocal accepted, close_code, messages_sent, bytes_sent
            await send(message)
            msg_type = message["type"]
            if msg_type == "websocket.accept":
                accepted = True
            elif msg_type == "websocket.send":
                messages_sent += 1
                bytes_sent += _ws_frame_bytes(message)
                request_state.forget_unrepeated_calls()
            elif msg_type == "websocket.close":
                close_code = int(message.get("code") or 1000)

        try:
            check_pools()
            await self.app(scope, ws_receive, ws_send)
        except WebSocketDisconnect as exc:
            # The client left and the handler let that propagate, as the usual
            # receive loop does. That is how a session ends, not a failure of it.
            if close_code is None:
                close_code = exc.code
            raise
        except Exception as exc:
            captured_error = exc
            if close_code is None:
                close_code = 1006
            raise
        finally:
            try:
                finished = time.perf_counter()
                # Cumulative process GC time spans a session's whole lifetime, so skip gc.pause.
                _tag_pauses(request_state, start, finished, include_gc=False)
                route_template = _extract_route_template(scope, request_state)
                if captured_error is not None:
                    level, msg = logging.ERROR, "websocket.failed"
                    keep_request_logs(failed=True)
                elif not accepted:
                    level, msg = logging.WARNING, "websocket.rejected"
                else:
                    level, msg = logging.INFO, "websocket.closed"
                if self.logger.isEnabledFor(level):
                    event: dict[str, Any] = {
                        "path": scope.get("path", ""),
                        "client_ip": client_ip,
                        "accepted": accepted,
                        "close_code": (
                            close_code if close_code is not None else (1000 if accepted else 403)
                        ),
                        "duration_ms": round(max(0.0, finished - start) * 1000, 3),
                        "messages_received": messages_received,
                        "messages_sent": messages_sent,
                        "bytes_received": bytes_received,
                        "bytes_sent": bytes_sent,
                    }
                    _attach_state_fields(event, route_template, request_state)
                    self.logger.log(
                        level, msg, exc_info=captured_error, extra={"event": event}
                    )
                self._emit_request_diagnostics("WS", route_template, request_state)
            finally:
                self.context_manager.cleanup(request_state)

    def _emit_request_diagnostics(
        self, metric_method: str, route_template: str, request_state: RequestState
    ) -> None:
        if request_state.statements:
            self._report_repeated_statement(
                metric_method, route_template, request_state
            )
        if request_state.http_targets:
            self._report_repeated_statement(
                metric_method, route_template, request_state, kind="http"
            )
        if request_state.slow_query is not None:
            fingerprint, seconds, origin = request_state.slow_query
            self._warn_once(
                "db.slow_query",
                metric_method,
                route_template,
                fingerprint,
                {"statement_fingerprint": fingerprint, "duration_ms": round(seconds * 1000),
                 **origin},
            )
        held = request_state.check_held_connection()
        if held is not None:
            name, details = held
            self._warn_once(
                name, metric_method, route_template, str(details.get("target", "")), details
            )
        if request_state.gc_pause_seconds is not None:
            report_diagnostic("gc.pause")

    def _observe_response(
        self, scope: Scope, method: str, status_code: int, duration_seconds: float,
        request_state: RequestState,
    ) -> None:
        if not isinstance(self.metrics_backend, NoopMetricsBackend):
            metric_method = method if method in _HTTP_METHODS else "_OTHER"
            self.metrics_recorder.observe(
                method=metric_method,
                path=_extract_route_template(scope, request_state),
                status_code=status_code,
                duration_seconds=max(0.0, duration_seconds),
                scope=scope,
            )
        if _METRIC_HOOKS:
            self.metrics_recorder.emit_hooks(
                scope=scope,
                status_code=status_code,
                duration_seconds=duration_seconds,
            )

    def _report_repeated_statement(
        self,
        method: str,
        route: str,
        state: RequestState,
        *,
        warn: bool = True,
        kind: CallKind = "db",
    ) -> None:
        """Warn once per method, route and statement/target when a call runs once per item (N+1)."""
        repeated = state.most_repeated_statement(kind)
        if repeated is None:
            return
        statement, count, origin = repeated
        if count < _N_PLUS_ONE_MIN_REPEATS:
            return
        diagnostic = f"{kind}.n_plus_one"
        fingerprint_key = "statement_fingerprint" if kind == "db" else "target_fingerprint"
        if kind == "db":
            state.add_diagnostic(diagnostic, {
                "count": count, "threshold": _N_PLUS_ONE_MIN_REPEATS,
                fingerprint_key: statement, **origin,
            })
        if not warn:
            return
        self._warn_once(
            diagnostic,
            method,
            route,
            statement,
            {fingerprint_key: statement, "count": count, **origin},
        )

    def _warn_once(
        self,
        diagnostic: str,
        method: str,
        route: str,
        dedup_id: str,
        details: dict[str, str | int],
    ) -> None:
        report_diagnostic(diagnostic, route)
        key = (diagnostic, method, route, dedup_id)
        if key in self._reported_statements:
            return
        if len(self._reported_statements) >= _MAX_REPORTED_STATEMENTS:
            del self._reported_statements[next(iter(self._reported_statements))]
        self._reported_statements[key] = None
        self.logger.warning(
            diagnostic,
            extra={"event": {"method": method, "route": route, **details}},
        )

__all__ = ["RequestLoggingMiddleware"]
