from __future__ import annotations

import asyncio

from fastapi import FastAPI
from pydantic import BaseModel

from fastapiobserver import (
    ObservabilitySettings,
    OTelSettings,
    SecurityPolicy,
    install_observability,
)

app = FastAPI()

install_observability(
    app,
    ObservabilitySettings(
        app_name="advanced-benchmark",
        service="advanced-benchmark",
        environment="benchmark",
        metrics_enabled=True,
    ),
    otel_settings=OTelSettings(
        enabled=True,
        service_name="advanced-benchmark",
        otlp_endpoint="http://localhost:4317",
        protocol="grpc",
    ),
    security_policy=SecurityPolicy(
        log_request_body=True,
        log_response_body=True,
    ),
)


class ItemPayload(BaseModel):
    name: str
    description: str | None = None
    price: float
    tax: float | None = None
    tags: list[str] = []


@app.post("/items")
async def create_item(item: ItemPayload) -> dict[str, str]:
    # Simulate database / external service latency
    await asyncio.sleep(0.015)
    return {"status": "created", "name": item.name}
