from fastapi import FastAPI

from observabilityfastapi import (
    ObservabilitySettings,
    SecurityPolicy,
    TrustedProxyPolicy,
    install_observability,
)
from observabilityfastapi.request_context import get_request_id

app = FastAPI(title="ObservabilityFastAPI Example")

settings = ObservabilitySettings(
    app_name="example-api",
    service="example",
    environment="development",
    version="0.1.0",
    metrics_enabled=True,
)

install_observability(
    app,
    settings,
    security_policy=SecurityPolicy(),
    trusted_proxy_policy=TrustedProxyPolicy(enabled=True),
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/items/{item_id}")
def read_item(item_id: int) -> dict[str, str | int | None]:
    return {
        "item_id": item_id,
        "request_id": get_request_id(),
    }
