import logging
from fastapi import FastAPI
from starlette.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from logging_setup import setup_logging
from middleware import RequestLoggingMiddleware

# Setup Custom JSON Logging
setup_logging()
logger = logging.getLogger(__name__)

app = FastAPI()

# Add Custom Middleware for metrics, logging, and trace context
app.add_middleware(RequestLoggingMiddleware)

# Manually wire up Prometheus endpoint
@app.get("/metrics", include_in_schema=False)
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

@app.get("/")
def read_root():
    logger.info("Handling root request")
    return {"message": "Hello World"}
