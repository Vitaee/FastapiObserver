from __future__ import annotations

import logging
import time

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..config import ObservabilitySettings
from ..metrics import MetricsBackend, NoopMetricsBackend
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
from .span_errors import _SpanErrorRecorder


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
        raw_headers = scope.get("headers", [])
        client_ip, trusted_source = self.ip_resolver.resolve(scope, raw_headers)
        request_id = self.context_manager.initialize(raw_headers, trusted_source)

        path = scope.get("path", "")
        method = scope.get("method", "UNKNOWN")
        status_code = 500
        captured_error: Exception | None = None
        request_content_type = _get_header(raw_headers, b"content-type")
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

        try:
            await self.app(scope, receive_wrapper, send_wrapper)
        except Exception as exc:
            had_error = True
            captured_error = exc
            status_code = 500
            self.span_error_recorder.record_exception(exc)
            raise
        finally:
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
            )

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
    ) -> None:
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
            scope=scope,
        )
        self.metrics_recorder.emit_hooks(
            scope=scope,
            status_code=status_code,
            duration_seconds=duration_seconds,
        )
        self.context_manager.cleanup()

__all__ = ["RequestLoggingMiddleware"]
