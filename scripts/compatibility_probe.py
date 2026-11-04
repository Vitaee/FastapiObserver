"""Print deterministic public-interface and FastAPI output evidence.

Run against the installed package, or set PYTHONPATH to an extracted release's
src directory. The checked-in fixture was captured from the v2.2.1 tag.
"""

from __future__ import annotations

import inspect
import json
import logging
from typing import Any


def snapshot() -> dict[str, Any]:
    import fastapiobserver as observer
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from fastapiobserver.logging import LOG_SCHEMA_VERSION

    signatures = {}
    for name in observer.__all__:
        value = getattr(observer, name)
        if inspect.isfunction(value):
            signatures[name] = [
                {
                    "name": p.name,
                    "kind": p.kind.name,
                    "default": repr(p.default) if p.default is not p.empty else "<required>",
                }
                for p in inspect.signature(value).parameters.values()
            ]

    settings = {}
    for model in (
        observer.ObservabilitySettings, observer.SecurityPolicy,
        observer.TrustedProxyPolicy, observer.OTelSettings,
        observer.OTelLogsSettings, observer.OTelMetricsSettings,
        observer.RuntimeControlSettings,
    ):
        settings[model.__name__] = {
            name: {"default": field.get_default(call_default_factory=True),
                   "aliases": str(field.validation_alias)}
            for name, field in model.model_fields.items()
        }

    payloads = []
    formatter = observer.StructuredJsonFormatter(observer.ObservabilitySettings())

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            if record.name == "fastapiobserver.middleware":
                payload = json.loads(formatter.format(record))
                for key in ("timestamp", "request_id", "library_version"):
                    payload.pop(key, None)
                event = payload.get("event", {})
                for key in ("duration_ms", "app_duration_ms"):
                    event.pop(key, None)
                if "error" in payload:
                    payload["error"] = {"type": payload["error"]["type"],
                                        "message": payload["error"]["message"]}
                payloads.append(payload)

    app = FastAPI()

    @app.get("/items/{item_id}")
    def item(item_id: str) -> dict[str, str]:
        observer.set_user_context({"tenant": "example", "password": "hidden"})
        return {"item_id": item_id}

    @app.get("/failure")
    def failure() -> None:
        raise ValueError("upstream rejected request")

    observer.install_observability(
        app, observer.ObservabilitySettings(), configure_logging=False,
        metrics_enabled=False, otel_settings=observer.OTelSettings(enabled=False),
        otel_logs_settings=observer.OTelLogsSettings(enabled=False),
        otel_metrics_settings=observer.OTelMetricsSettings(enabled=False),
        runtime_control_settings=observer.RuntimeControlSettings(enabled=False),
    )
    diagnostic_logger = logging.getLogger("fastapiobserver.middleware")
    old_level = diagnostic_logger.level
    capture = Capture()
    diagnostic_logger.addHandler(capture)
    diagnostic_logger.setLevel(logging.INFO)
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            assert client.get("/items/42").status_code == 200
            assert client.get("/missing").status_code == 404
            assert client.get("/failure").status_code == 500
    finally:
        diagnostic_logger.removeHandler(capture)
        diagnostic_logger.setLevel(old_level)

    # Normalize tuple defaults to the same JSON representation as the fixture.
    return json.loads(json.dumps({
        "exports": observer.__all__, "signatures": signatures,
        "settings": settings, "log_schema_version": LOG_SCHEMA_VERSION,
        "fastapi_records": payloads,
    }))


if __name__ == "__main__":
    print(json.dumps(snapshot(), indent=2, sort_keys=True))
