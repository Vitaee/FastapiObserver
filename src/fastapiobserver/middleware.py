from __future__ import annotations

import logging
import re
import time
import uuid
from typing import Any

from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .config import ObservabilitySettings
from .metrics import MetricsBackend, NoopMetricsBackend, normalize_path
from .plugins import emit_metric_hooks
from .request_context import (
    clear_request_id,
    clear_span_id,
    clear_trace_id,
    clear_user_context,
    set_request_id,
    set_span_id,
    set_trace_id,
)
from .security import (
    SecurityPolicy,
    TrustedProxyPolicy,
    is_body_capturable,
    is_trusted_client_ip,
    resolve_client_ip,
    sanitize_event,
)

REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
TRACEPARENT_PATTERN = re.compile(
    r"^[\da-f]{2}-([\da-f]{32})-([\da-f]{16})-[\da-f]{2}$",
    re.IGNORECASE,
)


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
        self.ip_resolver = _IpResolver(self.trusted_proxy_policy)
        self.context_manager = _RequestContextManager(self.settings)
        self.event_builder = _RequestEventBuilder(self.security_policy)
        self.metrics_recorder = _MetricsRecorder(
            settings=self.settings,
            metrics_backend=self.metrics_backend,
            logger=self.logger,
        )
        self.span_error_recorder = _SpanErrorRecorder(self.logger)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start = time.perf_counter()
        headers = _decode_headers(scope.get("headers", []))
        client_ip, trusted_source = self.ip_resolver.resolve(scope, headers)
        request_id = self.context_manager.initialize(headers, trusted_source)

        path = scope.get("path", "")
        method = scope.get("method", "UNKNOWN")
        status_code = 500
        captured_error: Exception | None = None
        request_content_type = headers.get("content-type")
        request_body_enabled = self.security_policy.log_request_body and is_body_capturable(
            request_content_type,
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

        async def receive_wrapper() -> Message:
            message = await receive()
            request_body_capture.capture_from_message(message, "http.request")
            return message

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message.get("status", 500))
                response_headers = _decode_headers(message.get("headers", []))
                if (
                    self.security_policy.log_response_body
                    and self.security_policy.body_capture_media_types is not None
                ):
                    response_content_type = response_headers.get("content-type")
                    response_body_capture.set_enabled(
                        is_body_capturable(response_content_type, self.security_policy)
                    )
                updated_headers = _upsert_header(
                    message.get("headers", []),
                    self.settings.response_request_id_header,
                    request_id,
                )
                message = dict(message)
                message["headers"] = updated_headers

            response_body_capture.capture_from_message(message, "http.response.body")
            await send(message)

        try:
            await self.app(scope, receive_wrapper, send_wrapper)
        except Exception as exc:
            had_error = True
            captured_error = exc
            status_code = 500
            self.span_error_recorder.record_exception(exc)
            raise
        finally:
            duration_seconds = max(0.0, time.perf_counter() - start)
            error_type = _classify_error(status_code, captured_error)
            safe_event = self.event_builder.build(
                method=method,
                path=path,
                status_code=status_code,
                duration_seconds=duration_seconds,
                client_ip=client_ip,
                request_body=request_body_capture.value,
                response_body=response_body_capture.value,
                error_type=error_type,
                exception=captured_error,
            )
            if had_error:
                self.logger.exception("request.failed", extra={"event": safe_event})
            elif status_code >= 500:
                self.logger.error("request.server_error", extra={"event": safe_event})
            elif status_code >= 400:
                self.logger.warning("request.client_error", extra={"event": safe_event})
            else:
                self.logger.info("request.completed", extra={"event": safe_event})

            # Prefer route template for bounded cardinality
            route_template = _extract_route_template(scope, path)

            self.metrics_recorder.observe(
                method=method,
                path=route_template,
                status_code=status_code,
                duration_seconds=duration_seconds,
            )
            self.metrics_recorder.emit_hooks(
                scope=scope,
                status_code=status_code,
                duration_seconds=duration_seconds,
            )
            self.context_manager.cleanup()


class _BodyCapture:
    def __init__(self, *, enabled: bool, max_length: int) -> None:
        self.enabled = enabled
        self.max_length = max_length
        self._buffer = bytearray()

    def capture_from_message(self, message: Message, message_type: str) -> None:
        if not self.enabled:
            return
        if message.get("type") != message_type:
            return
        body = message.get("body")
        if not body:
            return
        self._append(body if isinstance(body, bytes) else bytes(body))

    @property
    def value(self) -> str | None:
        if not self.enabled or not self._buffer:
            return None
        return self._buffer.decode("utf-8", "replace")

    def _append(self, chunk: bytes) -> None:
        remaining = self.max_length - len(self._buffer)
        if remaining <= 0:
            return
        self._buffer.extend(chunk[:remaining])

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled


class _IpResolver:
    def __init__(self, policy: TrustedProxyPolicy) -> None:
        self.policy = policy

    def resolve(self, scope: Scope, headers: dict[str, str]) -> tuple[str | None, bool]:
        scope_client_ip = _extract_scope_client_ip(scope)
        trusted_source = (
            is_trusted_client_ip(scope_client_ip, self.policy)
            if self.policy.enabled
            else True
        )
        client_ip = resolve_client_ip(scope_client_ip, headers, self.policy)
        return client_ip, trusted_source


class _RequestContextManager:
    def __init__(self, settings: ObservabilitySettings) -> None:
        self.settings = settings

    def initialize(self, headers: dict[str, str], trusted_source: bool) -> str:
        request_id = _resolve_request_id(
            headers.get(self.settings.request_id_header),
            trusted_source,
        )
        set_request_id(request_id)

        if trusted_source:
            trace_id, span_id = _parse_traceparent(headers.get("traceparent"))
            if trace_id:
                set_trace_id(trace_id)
            if span_id:
                set_span_id(span_id)
        return request_id

    def cleanup(self) -> None:
        clear_request_id()
        clear_trace_id()
        clear_span_id()
        clear_user_context()


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
            event["exception_message"] = str(exception)
        return sanitize_event(event, self.policy)


class _MetricsRecorder:
    def __init__(
        self,
        *,
        settings: ObservabilitySettings,
        metrics_backend: MetricsBackend,
        logger: logging.Logger,
    ) -> None:
        self.settings = settings
        self.metrics_backend = metrics_backend
        self.logger = logger

    def observe(
        self,
        *,
        method: str,
        path: str,
        status_code: int,
        duration_seconds: float,
    ) -> None:
        if path in self.settings.metrics_exclude_paths:
            return
        try:
            self.metrics_backend.observe(
                method=method,
                path=normalize_path(path),
                status_code=status_code,
                duration_seconds=duration_seconds,
            )
        except Exception:
            self.logger.exception(
                "metrics.observe.failed",
                extra={
                    "event": {"method": method, "path": path},
                    "_skip_enrichers": True,
                },
            )

    def emit_hooks(
        self,
        *,
        scope: Scope,
        status_code: int,
        duration_seconds: float,
    ) -> None:
        request = Request(scope)
        response = Response(status_code=status_code)
        emit_metric_hooks(request, response, duration_seconds)


class _SpanErrorRecorder:
    """Record unhandled request exceptions on the active OTel span when present."""

    def __init__(self, logger: logging.Logger) -> None:
        self.logger = logger

    def record_exception(self, error: Exception) -> None:
        try:
            from opentelemetry import trace as otel_trace
            from opentelemetry.trace import Status, StatusCode
        except ImportError:
            return

        try:
            span = otel_trace.get_current_span()
            if span is None:
                return
            span_context = span.get_span_context()
            if not span_context or not span_context.is_valid:
                return
            span.record_exception(error)
            span.set_status(Status(StatusCode.ERROR))
        except Exception:
            self.logger.debug(
                "otel.span.exception_record.failed",
                exc_info=True,
                extra={"_skip_enrichers": True},
            )


def _extract_scope_client_ip(scope: Scope) -> str | None:
    client = scope.get("client")
    if not client:
        return None
    if isinstance(client, (tuple, list)) and client:
        client_ip = client[0]
        if isinstance(client_ip, str):
            return client_ip
    return None


def _parse_traceparent(traceparent: str | None) -> tuple[str | None, str | None]:
    if not traceparent:
        return None, None
    match = TRACEPARENT_PATTERN.match(traceparent)
    if not match:
        return None, None
    return match.group(1), match.group(2)


def _resolve_request_id(candidate: str | None, trust_candidate: bool) -> str:
    if trust_candidate and candidate and REQUEST_ID_PATTERN.match(candidate):
        return candidate
    return str(uuid.uuid4())


def _upsert_header(
    headers: list[tuple[bytes, bytes]],
    key: str,
    value: str,
) -> list[tuple[bytes, bytes]]:
    key_bytes = key.lower().encode("latin1")
    value_bytes = value.encode("latin1", "replace")
    next_headers = [(k, v) for (k, v) in headers if k.lower() != key_bytes]
    next_headers.append((key_bytes, value_bytes))
    return next_headers


def _decode_headers(headers: list[tuple[bytes, bytes]]) -> dict[str, str]:
    return {
        key.decode("latin1").lower(): value.decode("latin1")
        for key, value in headers
    }


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

def _extract_route_template(scope: Scope, raw_path: str) -> str:
    """Return the matched route template for bounded metric cardinality.

    After Starlette's router runs, ``scope["route"]`` contains the matched
    ``Route`` object whose ``.path`` is the template string
    (e.g. ``/users/{user_id}``).

    Falls back to *raw_path* when:
    * No route matched (404 / unmatched sub-apps)
    * ``scope["route"]`` is not a Starlette ``Route`` with a ``path`` attr
    """
    route = scope.get("route")
    if route is not None:
        template = getattr(route, "path", None)
        if template:
            return template
    return raw_path


__all__ = ["RequestLoggingMiddleware"]
