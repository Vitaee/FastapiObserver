"""Keep selected event fields while retaining timestamp, identity and request ID.

Run: uvicorn examples.allowlist_app:app --reload
Try GET /users/42 and inspect its request.completed line.
Header allowlists apply when structured events include a headers mapping;
sensitive values that remain allowed are still redacted.
"""

from fastapi import FastAPI

from fastapiobserver import SecurityPolicy, install_observability

app = FastAPI(title="Allowlist Sanitization Example")
install_observability(
    app,
    security_policy=SecurityPolicy(
        header_allowlist=("x-request-id", "content-type", "user-agent", "traceparent"),
        event_key_allowlist=("method", "path", "status_code", "duration_ms", "error_type"),
    ),
)


@app.get("/users/{user_id}")
def get_user(user_id: int) -> dict[str, str | int]:
    return {"user_id": user_id, "name": "Alice"}
