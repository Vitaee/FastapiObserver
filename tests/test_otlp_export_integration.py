from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from fastapiobserver import OTelSettings, ObservabilitySettings, install_observability


def test_spans_are_exported_to_an_otlp_http_collector() -> None:
    trace_api = pytest.importorskip("opentelemetry.trace")
    pytest.importorskip("opentelemetry.exporter.otlp.proto.http.trace_exporter")
    received: list[tuple[str, bytes]] = []

    class Collector(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            received.append((self.path, self.rfile.read(int(self.headers["content-length"]))))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *_args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Collector)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        app = FastAPI()

        @app.get("/ping")
        def ping() -> dict[str, bool]:
            return {"ok": True}

        install_observability(
            app,
            ObservabilitySettings(app_name="orders-api", service="orders", environment="test"),
            metrics_enabled=False,
            otel_settings=OTelSettings(
                enabled=True,
                protocol="http/protobuf",
                otlp_endpoint=f"http://127.0.0.1:{server.server_port}",
            ),
        )
        assert TestClient(app).get("/ping").status_code == 200
        trace_api.get_tracer_provider().force_flush()
        deadline = time.monotonic() + 3
        while not received and time.monotonic() < deadline:
            time.sleep(0.05)
    finally:
        server.shutdown()
        server.server_close()

    assert any(path == "/v1/traces" and b"/ping" in body for path, body in received)
