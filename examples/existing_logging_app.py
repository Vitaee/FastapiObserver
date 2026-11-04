"""Keep application-owned JSON logging and redaction.

Run: uvicorn examples.existing_logging_app:app
Try GET /orders/42 and GET /failure. All personal data here is synthetic.
The small sanitizer below is illustrative; reuse your approved sanitizer and
its existing test vectors in a real application.
"""

from __future__ import annotations

import json
import logging
import logging.config
import re
from typing import Any

from fastapi import FastAPI

from fastapiobserver import install_observability, set_user_context
from fastapiobserver.logging import RequestIdFilter, TraceContextFilter
from fastapiobserver.security import strip_url_credentials

_EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
_TOKEN = re.compile(r"\btoken=[\w-]+")


def application_sanitizer(value: Any) -> Any:
    """Stand-in for the organisation's existing sanitizer, shared with its tests."""
    if isinstance(value, str):
        text = strip_url_credentials(value)
        return _TOKEN.sub("token=[redacted]", _EMAIL.sub("[redacted]", text))
    if isinstance(value, dict):
        return {
            key: "[redacted]" if key in {"email", "token"} else application_sanitizer(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [application_sanitizer(item) for item in value]
    return value


class ApplicationRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = application_sanitizer(record.getMessage())
        record.args = ()
        for field in ("event", "user_context"):
            if hasattr(record, field):
                setattr(record, field, application_sanitizer(getattr(record, field)))
        if record.exc_info:
            # Sanitize the rendered traceback too, before any handler can write
            # or export it. Do not leave a second, raw exception on the record.
            record.exc_text = application_sanitizer(
                logging.Formatter().formatException(record.exc_info)
            )
            record.exc_info = None
        return True


class ApplicationJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            **{
                field: getattr(record, field)
                for field in ("event", "request_id", "trace_id", "span_id", "user_context")
                if getattr(record, field, None) is not None
            },
        }
        if record.exc_text:
            payload["error"] = record.exc_text
        return json.dumps(payload)


# This configuration belongs to the application, including the server loggers.
# Correlation filters also enrich the application's own records. Request
# diagnostics already carry their correlation fields when they are emitted.
logging.config.dictConfig({
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"json": {"()": ApplicationJsonFormatter}},
    "filters": {
        "request": {"()": RequestIdFilter},
        "trace": {"()": TraceContextFilter},
        "redaction": {"()": ApplicationRedactionFilter},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
            "formatter": "json",
            "filters": ["request", "trace", "redaction"],
        },
    },
    "root": {"level": "INFO", "handlers": ["console"]},
    "loggers": {
        "uvicorn": {"handlers": [], "level": "INFO", "propagate": True},
        "uvicorn.access": {"handlers": [], "level": "INFO", "propagate": True},
    },
})

# On FastAPI 0.142+, direct OTel error logs bypass stdlib handler filters.
# This application sends its errors through its approved logging pipeline.
app = FastAPI(title="Application-owned logging", telemetry={"logs": False})
install_observability(app, configure_logging=False)
logger = logging.getLogger("orders")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/orders/{order_id}")
async def order(order_id: int) -> dict[str, int]:
    set_user_context({"tenant_slug": "example", "email": "demo@example.test"})
    logger.info(
        "order.accepted demo=%s token=%s", "demo@example.test", "example-secret",
        extra={"event": {"order_id": order_id, "token": "example-secret"}},
    )
    return {"order_id": order_id}


@app.get("/failure")
async def failure() -> None:
    raise RuntimeError("downstream rejected token=example-secret for demo@example.test")
