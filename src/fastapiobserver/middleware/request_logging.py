from __future__ import annotations

import logging
import time

from anyio import get_cancelled_exc_class
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..config import ObservabilitySettings
from ..logging.filters import RequestIdFilter, TraceContextFilter
from ..logging.held import keep_request_logs
from ..metrics import MetricsBackend, NoopMetricsBackend
from ..pools import check_pools
from ..request_context import get_request_state
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

# The same statement this many times in one request is usually a query per item
# in a loop (N+1), which one query with a join or IN would replace.
# ponytail: fixed threshold; make it a setting if it's too noisy or quiet for someone.
_N_PLUS_ONE_MIN_REPEATS = 10
_MAX_REPORTED_STATEMENTS = 1024
# Metric labels take the method only from this set, as OpenTelemetry does, so a
# client can't add a time series per made-up method.
_HTTP_METHODS = frozenset(
    {"GET", "HEAD", "POST", "PUT", "DELETE", "CONNECT", "OPTIONS", "TRACE", "PATCH"}
)
_CONTEXT_FILTERS = (RequestIdFilter(), TraceContextFilter())


class RequestLoggingMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        settings: ObservabilitySettings,
        security_policy: SecurityPolicy | None = None,
        trusted_proxy_policy: TrustedProxyPolicy | None = None,
        metrics_backend: MetricsBackend | None = None,
    ) -> None:
        self.app = app
        self.settings = settings
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
        self._reported_statements: dict[tuple[str, str, str], None] = {}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        check_pools()
        start = time.perf_counter()
        raw_headers = scope.get("headers", [])
        client_ip, trusted_source = self.ip_resolver.resolve(scope, raw_headers)
        request_id = self.context_manager.initialize(scope, raw_headers, trusted_source)

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
        response_started = False
        response_finished: float | None = None
        response_trailers = False
        disconnected_at: float | None = None

        async def receive_wrapper() -> Message:
            nonlocal disconnected_at
            message = await receive()
            if (message["type"] == "http.disconnect" and disconnected_at is None
                    and response_finished is None):
                disconnected_at = time.perf_counter()
            request_body_capture.capture_from_message(message, "http.request")
            return message

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code, response_started, response_finished, response_trailers
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
            await send(message)
            if message["type"] == "http.response.start":
                response_started = True
            finished = (
                message["type"] == "http.response.body"
                and not message.get("more_body", False)
                and not response_trailers
            ) or (
                message["type"] == "http.response.trailers"
                and not message.get("more_trailers", False)
            )
            if finished and response_finished is None:
                response_finished = time.perf_counter()
                self._observe_response(scope, method, status_code, response_finished - start)

        try:
            await self.app(
                scope,
                receive_wrapper,
                send_wrapper,
            )
        except Exception as exc:
            had_error = True
            captured_error = exc
            if not response_started:
                status_code = 500
            raise
        except get_cancelled_exc_class():
            # The client left, the server is shutting down or a timeout fired.
            # Not a server error: without a response, use nginx's 499.
            cancelled = True
            if not response_started:
                status_code = 499
            raise
        finally:
            try:
                if disconnected_at is not None and not response_started and not had_error:
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
                    response_finished=response_finished,
                    disconnected_at=disconnected_at,
                )
            finally:
                self.context_manager.cleanup()

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
        response_finished: float | None,
        disconnected_at: float | None,
    ) -> None:
        app_finished = time.perf_counter()
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

        request_state = get_request_state()
        failed = level >= logging.ERROR
        if failed or app_duration_seconds * 1000 >= self.settings.log_slow_request_ms:
            keep_request_logs(failed=failed)
        # Building and redacting the event is the costliest step; skip it when
        # the record would be dropped by the log level anyway.
        if self.logger.isEnabledFor(level):
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
                calls=request_state.calls if request_state else None,
            )
            safe_event["app_duration_ms"] = round(app_duration_seconds * 1000, 3)
            safe_event["response_completed"] = response_finished is not None
            safe_event["cancelled"] = cancelled
            safe_event["client_disconnected"] = disconnected_at is not None
            if disconnected_at is not None:
                safe_event["after_disconnect_ms"] = round(
                    max(0.0, app_finished - disconnected_at) * 1000, 3
                )
            if request_state is not None:
                for kind, errors in request_state.call_errors.items():
                    safe_event[kind]["errors"] = dict(errors)
            self.logger.log(
                level,
                message,
                exc_info=captured_error if had_error else None,
                extra={"event": safe_event},
            )

        route_template = _extract_route_template(scope)
        metric_method = method if method in _HTTP_METHODS else "_OTHER"
        if request_state is not None and request_state.statements:
            self._report_repeated_statement(
                metric_method, route_template, request_state.statements
            )

        if response_finished is None:
            self._observe_response(scope, method, status_code, duration_seconds)

    def _observe_response(
        self, scope: Scope, method: str, status_code: int, duration_seconds: float
    ) -> None:
        metric_method = method if method in _HTTP_METHODS else "_OTHER"
        self.metrics_recorder.observe(
            method=metric_method,
            path=_extract_route_template(scope),
            status_code=status_code,
            duration_seconds=max(0.0, duration_seconds),
            scope=scope,
        )
        self.metrics_recorder.emit_hooks(
            scope=scope,
            status_code=status_code,
            duration_seconds=duration_seconds,
        )

    def _report_repeated_statement(
        self, method: str, route: str, statements: dict[str, int]
    ) -> None:
        """Warn once per method, route and statement when a query runs once per item (N+1)."""
        statement, count = max(statements.items(), key=lambda item: item[1])
        key = (method, route, statement)
        if count < _N_PLUS_ONE_MIN_REPEATS or key in self._reported_statements:
            return
        if len(self._reported_statements) >= _MAX_REPORTED_STATEMENTS:
            del self._reported_statements[next(iter(self._reported_statements))]
        self._reported_statements[key] = None
        self.logger.warning(
            "db.n_plus_one",
            extra={
                "event": {
                    "method": method,
                    "route": route,
                    "statement_fingerprint": statement,
                    "count": count,
                },
            },
        )

__all__ = ["RequestLoggingMiddleware"]
