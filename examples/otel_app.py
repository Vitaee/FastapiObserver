"""Trace requests with your collector; configuration comes from the environment.

    OBS_OTEL_ENABLED=true \
    OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317 \
    OTEL_RESOURCE_ATTRIBUTES='team=backend,cloud.region=local' \
    uvicorn examples.otel_app:app --reload

Requires the otel extra. Try GET /orders/42 and find trace_id/span_id on its
JSON log lines. FastAPI's native tracing is used when available.
"""

import logging

from fastapi import FastAPI

from fastapiobserver import ObservabilitySettings, get_request_id, install_observability

app = FastAPI(title="OpenTelemetry Example")
logger = logging.getLogger("examples.orders")
install_observability(app, ObservabilitySettings(service="orders"))


@app.get("/orders/{order_id}")
def get_order(order_id: int) -> dict[str, str | int | None]:
    logger.info("order.lookup", extra={"event": {"order_id": order_id}})
    return {"order_id": order_id, "request_id": get_request_id()}
