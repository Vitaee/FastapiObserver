import json
import logging
import os
import sys
import time
import uuid
from contextvars import ContextVar
from typing import Callable, Awaitable

from fastapi import FastAPI, Request, Response
from fastapi.responses import PlainTextResponse
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest

# --- 1. Contextvars setup (Must be carefully managed to avoid leaks) ---
request_id_var: ContextVar[str] = ContextVar("request_id", default="")


# --- 2. Stdlib JSON Logging Formatter (Boilerplate) ---
class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        current_span = trace.get_current_span()
        trace_id = current_span.get_span_context().trace_id if current_span.is_recording() else 0
        span_id = current_span.get_span_context().span_id if current_span.is_recording() else 0

        log_obj = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "name": record.name,
            "message": record.getMessage(),
            "request_id": request_id_var.get(),
        }

        if trace_id:
            log_obj["trace_id"] = f"{trace_id:032x}"
            log_obj["span_id"] = f"{span_id:016x}"

        if hasattr(record, "request_info"):
            log_obj["request"] = record.request_info  # type: ignore

        return json.dumps(log_obj)


# --- 3. Logging Initialization ---
logger = logging.getLogger("api")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(JSONFormatter())
logger.handlers.clear()
logger.addHandler(handler)
# Disable uvicorn default loggers to prevent duplicate unformatted logs
logging.getLogger("uvicorn.access").handlers.clear()
logging.getLogger("uvicorn.access").propagate = False

# --- 4. Prometheus Initialization ---
REGISTRY = CollectorRegistry()
http_requests_total = Counter(
    "http_requests_total", "Total HTTP requests", ["method", "path", "status"], registry=REGISTRY
)
http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration",
    ["method", "path", "status"],
    registry=REGISTRY,
)

# --- 5. OTel Tracing Initialization (Requires shutdown hooks) ---
resource = Resource.create(attributes={"service.name": "hard-way-api"})
provider = TracerProvider(resource=resource)
otlp_exporter = OTLPSpanExporter(
    endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
)
provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
trace.set_tracer_provider(provider)

app = FastAPI()
FastAPIInstrumentor.instrument_app(app)


# --- 6. Hand-rolled Routing & Context Middleware ---
@app.middleware("http")
async def observability_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    # Set Request ID Context
    req_id = request.headers.get("x-request-id", str(uuid.uuid4()))
    token = request_id_var.set(req_id)

    start_time = time.perf_counter()

    # Try to resolve route template to prevent metrics cardinality explosion
    route_path = request.url.path
    for route in app.routes:
        match, _ = route.matches(request.scope)  # type: ignore
        if match.value == 2:  # Match.FULL
            route_path = getattr(route, "path", route_path)
            break

    # Resolve basic IP logic
    client_ip = request.client.host if request.client else "127.0.0.1"
    if "x-forwarded-for" in request.headers:
        client_ip = request.headers["x-forwarded-for"].split(",")[0].strip()

    # Request Logging
    request_info = {
        "method": request.method,
        "url": str(request.url),
        "client_ip": client_ip,
    }

    try:
        response = await call_next(request)
        status_code = response.status_code
    except Exception as exc:
        status_code = 500
        logger.error("Request failed", exc_info=exc, extra={"request_info": request_info})
        raise
    finally:
        duration = time.perf_counter() - start_time

        # Log resolution
        logger.info(
            "HTTP Request",
            extra={
                "request_info": {
                    **request_info,
                    "status_code": status_code,
                    "duration_ms": round(duration * 1000, 2),
                }
            },
        )

        # Metrics resolution
        http_requests_total.labels(method=request.method, path=route_path, status=status_code).inc()
        http_request_duration_seconds.labels(
            method=request.method, path=route_path, status=status_code
        ).observe(duration)

        # Clean up contextvar
        request_id_var.reset(token)

    response.headers["X-Request-ID"] = req_id
    return response


# --- 7. Metrics Endpoint ---
@app.get("/metrics", include_in_schema=False)
def metrics_endpoint() -> Response:
    return PlainTextResponse(generate_latest(REGISTRY))


# --- Application Logic ---
@app.get("/hello/{name}")
def say_hello(name: str) -> dict[str, str]:
    logger.info(f"Greeting {name}")
    return {"message": f"Hello {name}"}
