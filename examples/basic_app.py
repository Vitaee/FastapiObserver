"""
basic_app.py — Minimal example showing how fastapi-observer works.

Run this:
    uvicorn examples.basic_app:app --reload

Then try:
    curl http://localhost:8000/health
    curl http://localhost:8000/items/42
    curl http://localhost:8000/metrics   (Prometheus metrics)

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

from fastapiobserver import (
    ObservabilitySettings,
    SecurityPolicy,
    TrustedProxyPolicy,
    install_observability,
)
from fastapiobserver.request_context import get_request_id

# --- Step 1: Create your FastAPI app as usual ---
app = FastAPI(title="Basic Observability Example")


# --- Step 2: Define your observability settings ---
# These values appear in every log line, making it easy to filter logs
# in Grafana, Kibana, or any log aggregator.
settings = ObservabilitySettings(
    app_name="example-api",       # Name of your application
    service="example",            # Logical service name (used in metrics labels)
    environment="development",    # dev / staging / production
    version="0.1.0",              # Your app version (useful for canary deployments)
    metrics_enabled=True,         # Mount a /metrics endpoint for Prometheus
    
    # Enable Logtail Dead Letter Queue for best-effort local durability
    # dropped logs will be archived to `.dlq/logtail` as NDJSON files
    logtail_dlq_enabled=True,
)


# --- Step 3: Install observability in one call ---
# This single call sets up: structured logging, request middleware,
# Prometheus metrics endpoint, and security policies.
install_observability(
    app,
    settings,
    # SecurityPolicy controls what gets logged and what gets masked.
    # By default: passwords, tokens, authorization headers → "***"
    # Body logging is OFF by default (opt-in for safety).
    security_policy=SecurityPolicy(),
    # TrustedProxyPolicy controls whether to trust x-request-id headers
    # from incoming requests. Only IPs in trusted CIDRs are honored.
    # Default trusted: 10.x.x.x, 172.16.x.x, 192.168.x.x, 127.0.0.1
    trusted_proxy_policy=TrustedProxyPolicy(enabled=True),
)


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
