import logging
from fastapi import FastAPI
from fastapiobserver import ObservabilitySettings, OTelSettings, install_observability

# Application Logic First
app = FastAPI()
logger = logging.getLogger("api")


@app.get("/hello/{name}")
def say_hello(name: str) -> dict[str, str]:
    logger.info(f"Greeting {name}")
    return {"message": f"Hello {name}"}


# Observability attached declaratively at the end
install_observability(
    app,
    ObservabilitySettings(
        app_name="easy-way-api",
        service="easy-way-api",
        metrics_enabled=True,
    ),
    otel_settings=OTelSettings(
        enabled=True,
        service_name="easy-way-api",
        otlp_endpoint="http://localhost:4317",
    ),
)
