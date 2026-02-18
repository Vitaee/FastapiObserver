"""
otel_app.py — Demonstrates OpenTelemetry tracing integration.

Prerequisites:
    pip install "fastapi-observer[otel]"

Run this:
    uvicorn examples.otel_app:app --reload

Or with environment variables:
    OTEL_ENABLED=true \
    OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317 \
    OTEL_EXTRA_RESOURCE_ATTRIBUTES="k8s.namespace=prod,team=platform" \
    uvicorn examples.otel_app:app --reload

What happens under the hood when OTel is enabled:

    1. install_otel() checks if a TracerProvider already exists.
       - If your app or another library already set one up, fastapi-observer
         will REUSE it instead of creating a duplicate. This prevents the
         common "double-tracing" problem.
       - If no provider exists, it creates one with:
         • A DynamicTraceIdRatioSampler (sampling ratio adjustable at runtime)
         • A BatchSpanProcessor that batches spans and exports them
         • An OTLPSpanExporter that sends spans to your collector

    2. FastAPIInstrumentor.instrument_app() wraps your FastAPI app.
       Every incoming request automatically creates a trace span with:
       - HTTP method, URL, status code, user agent
       - A unique trace_id (32 hex chars) and span_id (16 hex chars)

    3. LoggingInstrumentor.instrument() patches Python's logging module.
       Every log.info() / log.warning() / etc. automatically gets
       trace_id and span_id injected, even in your business logic code.

    4. The TraceContextFilter in the JSON formatter reads the current
       OTel span and injects trace_id/span_id into every structured log.
       This means your JSON logs in stdout/Loki/etc. are automatically
       correlated with your traces in Jaeger/Tempo/etc.

    5. extra_resource_attributes lets you add custom metadata to every span.
       Common use cases:
       - k8s.namespace, k8s.pod.name    → Kubernetes context
       - cloud.provider, cloud.region   → Cloud provider metadata
       - team, cost_center              → Business metadata
       These attributes show up in your trace viewer (Jaeger, Tempo, etc.)
       and can be used for filtering and grouping.

    6. The runtime control plane lets you adjust trace_sampling_ratio
       without restarting:
         curl -X POST http://localhost:8000/_observability/control \
           -H "Authorization: Bearer $TOKEN" \
           -d '{"trace_sampling_ratio": 0.1}'
       This is useful when you need to reduce tracing volume during
       high-traffic events.
"""

import logging

from fastapi import FastAPI

from fastapiobserver import (
    ObservabilitySettings,
    SecurityPolicy,
    TrustedProxyPolicy,
    install_observability,
)
from fastapiobserver.otel import OTelSettings
from fastapiobserver.request_context import get_request_id, get_trace_id, get_span_id

app = FastAPI(title="OTel Tracing Example")
logger = logging.getLogger("examples.otel_app")

settings = ObservabilitySettings(
    app_name="orders-api",
    service="orders",
    environment="development",
    version="2.0.0",
    metrics_enabled=True,
)

# --- OTel settings ---
# You can define these in code or via environment variables.
# Environment variables take precedence when using OTelSettings.from_env().
otel_settings = OTelSettings(
    enabled=True,
    service_name="orders-api",
    service_version="2.0.0",
    environment="development",
    # Where to send traces. Common endpoints:
    #   gRPC: http://localhost:4317  (default)
    #   HTTP: http://localhost:4318/v1/traces
    otlp_endpoint="http://localhost:4317",
    protocol="grpc",            # "grpc" or "http/protobuf"
    trace_sampling_ratio=1.0,   # 1.0 = trace everything, 0.1 = 10% of requests
    # Add any custom attributes to every span.
    # These appear in Jaeger/Tempo and help with filtering.
    extra_resource_attributes={
        "k8s.namespace": "default",
        "team": "backend",
    },
)

install_observability(
    app,
    settings,
    security_policy=SecurityPolicy(),
    trusted_proxy_policy=TrustedProxyPolicy(enabled=True),
    otel_settings=otel_settings,
)


@app.get("/orders/{order_id}")
def get_order(order_id: int) -> dict[str, str | int | None]:
    """
    When OTel is active, this endpoint's log will contain:
      "trace_id": "a1b2c3d4e5f6...",
      "span_id": "1a2b3c4d..."

    These IDs match the trace in your Jaeger/Tempo/etc. dashboard.
    You can click from a log line in Grafana straight to the trace.
    """
    # This log line automatically gets trace_id and span_id injected.
    # No need to pass them manually!
    logger.info("Fetching order", extra={"event": {"order_id": order_id}})

    return {
        "order_id": order_id,
        "request_id": get_request_id(),
        "trace_id": get_trace_id(),
        "span_id": get_span_id(),
    }
