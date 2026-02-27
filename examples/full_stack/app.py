"""
Full-Stack Observability Demo — Multi-Instance FastAPI Application.

This single file powers all 3 services (app-a, app-b, app-c) in the
docker-compose stack. Each service gets its own identity via environment
variables (SERVICE_NAME, APP_NAME, etc.).

Run standalone:
    uvicorn examples.full_stack.app:app --reload

Or via docker-compose (recommended):
    cd examples/full_stack && docker compose up --build
"""

import asyncio
import logging
import os
import random

import httpx
from fastapi import FastAPI, HTTPException

from fastapiobserver import (
    ObservabilitySettings,
    get_request_id,
    get_span_id,
    get_trace_id,
    install_observability,
    register_log_enricher,
    register_metric_hook,
)
from fastapiobserver.otel import OTelLogsSettings, OTelSettings

# ---------------------------------------------------------------------------
# App identity from environment (each container gets different values)
# ---------------------------------------------------------------------------
SERVICE_NAME = os.getenv("SERVICE_NAME", "demo-api")
APP_NAME = os.getenv("APP_NAME", "demo")
ENVIRONMENT = os.getenv("ENVIRONMENT", "local")
APP_VERSION = os.getenv("APP_VERSION", "1.0.0")

# Cross-service targets for the /chain endpoint
TARGET_B = os.getenv("TARGET_B", "http://app-b:8000")
TARGET_C = os.getenv("TARGET_C", "http://app-c:8000")

app = FastAPI(title=f"{SERVICE_NAME} — Observability Demo")
logger = logging.getLogger(SERVICE_NAME)

# ---------------------------------------------------------------------------
# Observability settings — all configured via env vars
# ---------------------------------------------------------------------------
settings = ObservabilitySettings(
    app_name=APP_NAME,
    service=SERVICE_NAME,
    environment=ENVIRONMENT,
    version=APP_VERSION,
    metrics_enabled=True,
)

otel_settings = OTelSettings.from_env(settings)
otel_logs_settings = OTelLogsSettings.from_env()

install_observability(
    app,
    settings,
    otel_settings=otel_settings,
    otel_logs_settings=otel_logs_settings,
)


# ---------------------------------------------------------------------------
# Plugin hooks — demonstrate extensibility
# ---------------------------------------------------------------------------
def add_service_metadata(payload: dict) -> dict:
    """Enricher: add custom fields to every log line."""
    payload["deployment_region"] = os.getenv("DEPLOYMENT_REGION", "local")
    return payload


register_log_enricher("service_metadata", add_service_metadata)


def track_slow_requests(
    request: object, response: object, duration: float
) -> None:
    """Metric hook: log slow requests."""
    if duration > 1.0:
        logger.warning(
            "slow_request_detected",
            extra={"event": {"duration_seconds": round(duration, 3)}},
        )


register_metric_hook("slow_tracker", track_slow_requests)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/health")
def health() -> dict:
    """Health check — excluded from metrics by default."""
    return {"status": "ok", "service": SERVICE_NAME}


@app.get("/items/{item_id}")
def get_item(item_id: int) -> dict:
    """Standard item lookup with structured logging."""
    logger.info("item.lookup", extra={"event": {"item_id": item_id}})
    return {
        "item_id": item_id,
        "service": SERVICE_NAME,
        "request_id": get_request_id(),
        "trace_id": get_trace_id(),
        "span_id": get_span_id(),
    }


@app.get("/users/{user_id}")
def get_user(user_id: int) -> dict:
    """User lookup — separate route for per-route dashboard breakdown."""
    logger.info("user.lookup", extra={"event": {"user_id": user_id}})
    return {
        "user_id": user_id,
        "name": f"User {user_id}",
        "service": SERVICE_NAME,
        "request_id": get_request_id(),
    }


@app.get("/slow")
async def slow_endpoint() -> dict:
    """Simulates a slow response (0.5–2s) for latency histogram."""
    delay = random.uniform(0.5, 2.0)
    logger.info("slow.processing", extra={"event": {"delay_seconds": round(delay, 3)}})
    await asyncio.sleep(delay)
    return {"delay_seconds": round(delay, 3), "service": SERVICE_NAME}


@app.get("/error")
def error_endpoint() -> dict:
    """Randomly returns 4xx or 5xx errors for error rate panel."""
    roll = random.random()
    if roll < 0.3:
        raise HTTPException(status_code=400, detail="Bad request (simulated)")
    if roll < 0.5:
        raise HTTPException(status_code=404, detail="Not found (simulated)")
    if roll < 0.7:
        raise HTTPException(status_code=500, detail="Internal error (simulated)")
    return {"status": "lucky", "service": SERVICE_NAME}


@app.get("/chain")
async def chain() -> dict:
    """Cross-service call — demonstrates distributed tracing.

    app-a calls app-b/items/1 and app-c/users/1 concurrently.
    OpenTelemetry propagates the trace context automatically via httpx
    instrumentation, so all spans are linked under a single trace.
    """
    results = {}
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp_b, resp_c = await asyncio.gather(
            client.get(f"{TARGET_B}/items/1"),
            client.get(f"{TARGET_C}/users/1"),
            return_exceptions=True,
        )
        results["app_b"] = (
            resp_b.json()
            if not isinstance(resp_b, Exception)
            else {"error": str(resp_b)}
        )
        results["app_c"] = (
            resp_c.json()
            if not isinstance(resp_c, Exception)
            else {"error": str(resp_c)}
        )

    logger.info(
        "chain.completed",
        extra={"event": {"targets": [TARGET_B, TARGET_C]}},
    )
    return {
        "source": SERVICE_NAME,
        "trace_id": get_trace_id(),
        "results": results,
    }
