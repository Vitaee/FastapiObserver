"""Minimal setup. Run: uvicorn examples.basic_app:app --reload

Try GET /items/42, or GET /crash for a structured exception log.
Enable /metrics with OBS_METRICS_ENABLED=true and the prometheus extra.
"""

from fastapi import FastAPI

from fastapiobserver import get_request_id, install_observability

app = FastAPI(title="Basic Observability Example")
install_observability(app)  # Reads OBS_* settings and manages its own lifespan.


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/items/{item_id}")
def read_item(item_id: int) -> dict[str, str | int | None]:
    return {"item_id": item_id, "request_id": get_request_id()}


@app.get("/crash")
def crash() -> None:
    # The log carries error.type, error.stacktrace and a stable error.fingerprint.
    raise RuntimeError("Intentional example failure")
