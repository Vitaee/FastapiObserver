import logging
from fastapi import FastAPI
from fastapiobserver import ObservabilitySettings, install_observability

# No extra middleware, logging setup, or metrics wrapper files needed!

app = FastAPI()

# 1. Provide the bare minimum configuration
obs_settings = ObservabilitySettings(
    app_name="simple-api-migrated",
    environment="production",
    version="1.0.0",
    metrics_enabled=True,
    # OTel tracing is opt-in (for example: OTelSettings(enabled=True) or OTEL_ENABLED=true).
)

# 2. One line to install all observability features
install_observability(app, obs_settings)

logger = logging.getLogger(__name__)

@app.get("/")
def read_root():
    logger.info("Handling root request in the migrated app")
    return {"message": "Notice how metrics are at /metrics, and logs are structured JSON."}
