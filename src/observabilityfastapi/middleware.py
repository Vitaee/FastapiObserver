from __future__ import annotations

import logging
import re
import time
import uuid
from typing import Any

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
    set_trace_id,
)
from .security import (
    SecurityPolicy,
    TrustedProxyPolicy,
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
        self.logger = logging.getLogger("observabilityfastapi.middleware")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start = time.perf_counter()
        raw_headers = scope.get("headers", [])
        headers = {
            key.decode("latin1").lower(): value.decode("latin1")
            for key, value in raw_headers
        }

        scope_client_ip = _extract_scope_client_ip(scope)
        trusted_source = (
            is_trusted_client_ip(scope_client_ip, self.trusted_proxy_policy)
            if self.trusted_proxy_policy.enabled
            else True
        )
        client_ip = resolve_client_ip(scope_client_ip, headers, self.trusted_proxy_policy)

        request_id = _resolve_request_id(
            headers.get(self.settings.request_id_header.lower()), trusted_source
        )
        set_request_id(request_id)

        if trusted_source:
            trace_id, span_id = _parse_traceparent(headers.get("traceparent"))
            if trace_id:
                set_trace_id(trace_id)
            if span_id:
                # We only expose span id through context filters if available.
                from .request_context import set_span_id

                set_span_id(span_id)

        path = scope.get("path", "")
        method = scope.get("method", "UNKNOWN")
        status_code = 500
        request_body_capture = bytearray()
        response_body_capture = bytearray()
        had_error = False

        async def receive_wrapper() -> Message:
            message = await receive()
            if (
                self.security_policy.log_request_body
                and message.get("type") == "http.request"
                and message.get("body")
            ):
                _append_with_limit(
                    request_body_capture,
                    message.get("body", b""),
                    self.security_policy.max_body_length,
                )
            return message

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message.get("status", 500))
                updated_headers = _upsert_header(
                    message.get("headers", []),
                    self.settings.response_request_id_header,
                    request_id,
                )
                message = dict(message)
                message["headers"] = updated_headers

            if (
                self.security_policy.log_response_body
                and message["type"] == "http.response.body"
                and message.get("body")
            ):
                _append_with_limit(
                    response_body_capture,
                    message.get("body", b""),
                    self.security_policy.max_body_length,
                )
            await send(message)

        try:
            await self.app(scope, receive_wrapper, send_wrapper)
        except Exception:
            had_error = True
            status_code = 500
            raise
        finally:
            duration_seconds = max(0.0, time.perf_counter() - start)
            event: dict[str, Any] = {
                "method": method,
                "path": path,
                "status_code": status_code,
                "duration_ms": round(duration_seconds * 1000, 3),
                "client_ip": client_ip,
            }

            if self.security_policy.log_request_body and request_body_capture:
                event["request_body"] = request_body_capture.decode("utf-8", "replace")
            if self.security_policy.log_response_body and response_body_capture:
                event["response_body"] = response_body_capture.decode("utf-8", "replace")

            safe_event = sanitize_event(event, self.security_policy)
            if had_error:
                self.logger.exception("request.failed", extra={"event": safe_event})
            else:
                self.logger.info("request.completed", extra={"event": safe_event})

            if path not in self.settings.metrics_exclude_paths:
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

            request = Request(scope)
            response = Response(status_code=status_code)
            emit_metric_hooks(request, response, duration_seconds)

            clear_request_id()
            clear_trace_id()
            clear_span_id()
            clear_user_context()


def _append_with_limit(target: bytearray, chunk: bytes, max_length: int) -> None:
    remaining = max_length - len(target)
    if remaining <= 0:
        return
    target.extend(chunk[:remaining])


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


__all__ = ["RequestLoggingMiddleware"]
