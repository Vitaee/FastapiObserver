"""
basic_app.py — Minimal example showing how fastapi-observer works.

Run this:
    uvicorn examples.basic_app:app --reload

Then try:
    curl http://localhost:8000/health
    curl http://localhost:8000/items/42
    # Metrics are disabled by default in zero-glue mode.
    # Enable them with METRICS_ENABLED=true, then:
    # curl http://localhost:8000/metrics

Watch your terminal — every request produces a structured JSON log line with:
  - A unique request_id (for tracing a request across your system)
  - Method, path, status code, duration
  - Service metadata (app_name, service, environment, version)

What happens under the hood when you call install_observability():
  1. Logging is configured with a JSON formatter that writes to stdout
     (and optionally to a file). The formatter uses a background thread
     (QueueHandler → QueueListener) so log I/O never blocks your request.
  2. A middleware is added to your FastAPI app. For every HTTP request,
     this middleware:
       a. Generates or trusts an incoming x-request-id header
       b. Starts a performance timer
       c. Passes the request through your app
       d. On completion: logs the request event, records Prometheus metrics
  3. A /metrics endpoint is mounted (if metrics_enabled=True) so
     Prometheus can scrape your app's counters and histograms.
"""

from fastapi import FastAPI

from contextlib import asynccontextmanager

from fastapiobserver import (
    install_observability,
    observability_lifespan,
)
from fastapiobserver.request_context import get_request_id

# --- Step 1: Create your FastAPI app as usual ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    async with observability_lifespan(app):
        yield

app = FastAPI(title="Basic Observability Example", lifespan=lifespan)


# --- Step 2: Install observability in one call (Zero-Glue) ---
# Since we aren't passing `settings`, `security_policy`, etc. manually,
# `install_observability` will read from environment variables.
#
# Try running this app with different profiles:
#   OBS_PROFILE=development uvicorn examples.basic_app:app --reload
#   OBS_PROFILE=production uvicorn examples.basic_app:app --reload
#   METRICS_ENABLED=true uvicorn examples.basic_app:app --reload
install_observability(app)


# --- Step 4: Write your endpoints as normal ---
@app.get("/health")
def health() -> dict[str, str]:
    """Simple health check. Excluded from metrics by default."""
    return {"status": "ok"}


@app.get("/items/{item_id}")
def read_item(item_id: int) -> dict[str, str | int | None]:
    """
    Every request automatically gets a request_id.
    You can access it anywhere in your code via get_request_id().
    This is useful for correlating logs across multiple services:

        Frontend → API Gateway → This Service → Database
        All share the same request_id in their logs.
    """
    return {
        "item_id": item_id,
        "request_id": get_request_id(),
    }


@app.get("/crash")
def crash() -> dict[str, str]:
    """
    Demonstrates the Advanced AST-based Error Fingerprinting.
    Exceptions automatically generate a stable hash ignoring memory addresses
    and exact line numbers, included as `error.fingerprint` in the JSON log.
    """
    raise RuntimeError("Simulated crash to demonstrate AST error grouping.")
