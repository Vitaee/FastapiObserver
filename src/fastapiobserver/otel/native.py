"""FastAPI's native OpenTelemetry (FastAPI 0.142+), set up to fit fastapi-observer.

FastAPI takes these settings only as ``FastAPI(telemetry=...)`` and keeps them in
``app._telemetry``, which it reads on every request. That attribute isn't public
API. Without it, as before 0.142, nothing here changes anything, and tracing
falls back to OpenTelemetry's FastAPI instrumentation.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any


def native_telemetry_config(app: Any) -> MutableMapping[str, Any] | None:
    config = getattr(app, "_telemetry", None)
    return config if isinstance(config, MutableMapping) else None


def trace_natively(app: Any, excluded_urls_csv: str | None) -> bool:
    """Let FastAPI trace requests itself, skipping the excluded URLs.

    False when it can't: FastAPI before 0.142, or tracing turned off in
    ``FastAPI(telemetry=...)``.
    """
    config = native_telemetry_config(app)
    if config is None or not config.get("tracing", True):
        return False
    from .exclusions import NativeExclusions

    config["exclude"] = NativeExclusions(excluded_urls_csv)
    return True


def keep_native_logs_out(app: Any) -> None:
    """Stop FastAPI exporting its own logs around fastapi-observer's redaction.

    FastAPI writes validation failures and unhandled exceptions, with messages
    and stack traces, straight to the global logger provider, the one OTLP log
    export sets up. fastapi-observer already logs both, redacted.
    """
    config = native_telemetry_config(app)
    if config is not None:
        config["logs"] = False
