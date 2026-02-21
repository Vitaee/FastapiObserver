from __future__ import annotations

import asyncio
import os

from fastapi import FastAPI
from pydantic import BaseModel



app = FastAPI()

# S0: Baseline ()
# S1: Observer Minimal (Metrics=False, OTel=False, BodyCapture=False)
# S2: Metrics Only
# S3: Tracing Only
# S4: ALL (Metrics + Tracing + BodyCapture)
# S5: ALL (Collector Down) - Handled by the orchestrator shutting down Jaeger

SCENARIO = os.getenv("BENCHMARK_SCENARIO", "S0")

if SCENARIO != "S0":
    from fastapiobserver import (
        ObservabilitySettings,
        OTelSettings,
        SecurityPolicy,
        install_observability,
    )

    metrics_enabled = SCENARIO in ("S2", "S4", "S5")
    tracing_enabled = SCENARIO in ("S3", "S4", "S5")
    body_capture = SCENARIO in ("S4", "S5")

    install_observability(
        app,
        ObservabilitySettings(
            app_name="benchmark-api",
            service="benchmark-api",
            environment="benchmarking",
            metrics_enabled=metrics_enabled,
        ),
        otel_settings=OTelSettings(
            enabled=tracing_enabled,
            service_name="benchmark-api",
            otlp_endpoint="http://localhost:4317",
            protocol="grpc",
        ),
        security_policy=SecurityPolicy(
            log_request_body=body_capture,
            log_response_body=body_capture,
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
    # Simulate database / external service latency (I/O)
    await asyncio.sleep(0.015)
    return {"status": "created", "name": item.name}
