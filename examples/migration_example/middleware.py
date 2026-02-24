"""
Pure ASGI middleware for request/response logging, request ID correlation,
and Prometheus metrics collection.
"""
import logging
from time import perf_counter
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from metrics import (
    HTTP_REQUEST_DURATION,
    HTTP_REQUESTS_IN_PROGRESS,
    HTTP_REQUESTS_TOTAL,
    normalize_path,
)
from request_context import generate_request_id, set_request_id

class RequestLoggingMiddleware:
    REQUEST_ID_HEADER = b"x-request-id"

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._logger = logging.getLogger("demo.api")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start = perf_counter()
        request = Request(scope, receive)
        request_id = self._extract_request_id(scope) or generate_request_id()
        set_request_id(request_id)
        
        handler = normalize_path(request.url.path)
        method = request.method
        HTTP_REQUESTS_IN_PROGRESS.inc()
        
        response_status = 0
        async def send_wrapper(message: Message) -> None:
            nonlocal response_status
            if message["type"] == "http.response.start":
                response_status = message["status"]
                headers = list(message.get("headers", []))
                headers.append((self.REQUEST_ID_HEADER, request_id.encode()))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            elapsed = perf_counter() - start
            HTTP_REQUESTS_IN_PROGRESS.dec()
            HTTP_REQUEST_DURATION.labels(handler=handler, method=method).observe(elapsed)
            HTTP_REQUESTS_TOTAL.labels(handler=handler, method=method, status=str(response_status)).inc()
            
            http_details = {
                "method": method,
                "path": request.url.path,
                "status_code": response_status,
                "duration_ms": round(elapsed * 1000, 2),
                "client_ip": self._get_client_ip(request),
            }
            self._logger.info("http_access", extra={"http": http_details})

    def _extract_request_id(self, scope: Scope) -> str | None:
        headers = scope.get("headers", [])
        for name, value in headers:
            if name.lower() == self.REQUEST_ID_HEADER:
                return value.decode("utf-8")
        return None

    def _get_client_ip(self, request: Request) -> str | None:
        return request.client.host if request.client else None
