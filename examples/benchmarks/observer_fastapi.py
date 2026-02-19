from __future__ import annotations

from fastapi import FastAPI

from fastapiobserver import ObservabilitySettings, install_observability

app = FastAPI()
install_observability(
    app,
    ObservabilitySettings(
        app_name="benchmark-app",
        service="benchmark",
        environment="benchmark",
        metrics_enabled=False,
    ),
)


@app.get("/ping")
def ping() -> dict[str, str]:
    return {"status": "ok"}
