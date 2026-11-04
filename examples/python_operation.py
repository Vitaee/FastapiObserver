"""Run with: python examples/python_operation.py

Requires SQLAlchemy and HTTPX. Uses local SQLite and a loopback HTTP service;
no application framework, telemetry provider or external backend is needed.
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from threading import Thread

import httpx
from sqlalchemy import create_engine, text

from fastapiobserver import ObservabilitySettings, get_request_id, setup_logging
from fastapiobserver.db_queries import count_queries
from fastapiobserver.experimental import observe_operation
from fastapiobserver.logging import flush_logging, shutdown_logging
from fastapiobserver.propagation import instrument_httpx_client


class LocalService(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = json.dumps({"request_id": self.headers.get("x-request-id")}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        pass


def main() -> None:
    engine = create_engine("sqlite://")
    count_queries(engine)
    server = ThreadingHTTPServer(("127.0.0.1", 0), LocalService)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    setup_logging(ObservabilitySettings(service="catalog-import"))
    try:
        with httpx.Client(trust_env=False) as client:
            instrument_httpx_client(client)
            with observe_operation("catalog.import"):
                with engine.connect() as connection:
                    connection.execute(text("SELECT :number"), {"number": 1})
                    connection.execute(text("SELECT :number"), {"number": 2})
                response = client.get(f"http://127.0.0.1:{server.server_port}/catalog")
                response.raise_for_status()
                assert response.json()["request_id"] == get_request_id()
                logging.getLogger("catalog").info("catalog.imported", extra={"event": {"items": 2}})
        assert flush_logging()
    finally:
        shutdown_logging()
        engine.dispose()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


if __name__ == "__main__":
    main()
