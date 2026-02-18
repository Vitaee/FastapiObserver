from __future__ import annotations

import gzip
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest


@dataclass
class _RequestPayload:
    path: str
    headers: dict[str, str]
    body: bytes


class OtlpCollector:
    def __init__(self, host: str, port: int) -> None:
        self._host = host
        self._port = port
        self._lock = threading.Lock()
        self._requests: list[_RequestPayload] = []

    @property
    def endpoint(self) -> str:
        return f"http://{self._host}:{self._port}"

    @property
    def paths(self) -> list[str]:
        with self._lock:
            return [item.path for item in self._requests]

    @property
    def span_count(self) -> int:
        return len(self.get_spans())

    def add_request(self, path: str, headers: dict[str, str], body: bytes) -> None:
        with self._lock:
            self._requests.append(_RequestPayload(path=path, headers=headers, body=body))

    def get_spans(self) -> list[Any]:
        from opentelemetry.proto.collector.trace.v1 import trace_service_pb2

        with self._lock:
            payloads = list(self._requests)

        spans: list[Any] = []
        for payload in payloads:
            raw_body = payload.body
            if payload.headers.get("content-encoding", "").lower() == "gzip":
                try:
                    raw_body = gzip.decompress(raw_body)
                except OSError:
                    continue

            request = trace_service_pb2.ExportTraceServiceRequest()
            try:
                request.ParseFromString(raw_body)
            except Exception:
                continue

            for resource_span in request.resource_spans:
                for scope_span in resource_span.scope_spans:
                    spans.extend(scope_span.spans)
        return spans


@pytest.fixture
def otlp_collector() -> OtlpCollector:
    pytest.importorskip("opentelemetry.proto.collector.trace.v1.trace_service_pb2")

    collector: OtlpCollector | None = None

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - required method name
            content_length = int(self.headers.get("content-length", "0"))
            body = self.rfile.read(content_length)
            headers = {key.lower(): value for key, value in self.headers.items()}
            assert collector is not None
            collector.add_request(self.path, headers, body)
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, format: str, *args: object) -> None:  # noqa: A003
            _ = (format, args)
            return

    try:
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    except PermissionError:
        pytest.skip("Local socket binding is not allowed in this environment")
    host, port = server.server_address[:2]
    collector = OtlpCollector(host, int(port))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        yield collector
    finally:
        server.shutdown()
        thread.join(timeout=2.0)
        server.server_close()
