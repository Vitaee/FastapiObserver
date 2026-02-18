"""
allowlist_app.py — Demonstrates allowlist-only sanitization.

Run this:
    uvicorn examples.allowlist_app:app --reload

Then try:
    curl http://localhost:8000/users/42

What happens under the hood with allowlists:

    The default behavior is a BLOCKLIST approach:
      "Log everything, but redact known-sensitive keys."
      → password, token, authorization headers get masked to "***"

    Allowlists flip this to a WHITELIST approach:
      "Drop everything EXCEPT explicitly allowed keys."
      → Only keys you list will appear in logs.

    There are two independent allowlists:

    1. header_allowlist — Controls which HTTP headers appear in logs.
       When set, ANY header not in the list is silently dropped.
       Example: header_allowlist=("x-request-id", "content-type", "user-agent")
       → Authorization, Cookie, X-Api-Key... all gone from logs.

    2. event_key_allowlist — Controls which top-level event keys appear.
       When set, only listed keys survive in the log event dict.
       Example: event_key_allowlist=("method", "path", "status_code")
       → client_ip, duration_ms, request_body... all dropped.

    The allowlist is applied BEFORE redaction:
      1) Allowlist filters out unlisted keys
      2) Redaction masks any remaining sensitive keys
      This means you can allowlist "authorization" but still have it masked
      by the redaction rules — defense in depth.

    Why use allowlists?

    For strict compliance environments (SOC 2, HIPAA, PCI-DSS), auditors
    may require that you prove ONLY specific data is logged. A blocklist
    can miss new fields; an allowlist guarantees nothing unexpected leaks.

    You can also set allowlists via environment variables:
      export OBS_HEADER_ALLOWLIST="x-request-id,content-type,user-agent"
      export OBS_EVENT_KEY_ALLOWLIST="method,path,status_code,duration_ms"
"""

from fastapi import FastAPI

from fastapiobserver import (
    ObservabilitySettings,
    SecurityPolicy,
    TrustedProxyPolicy,
    install_observability,
)

app = FastAPI(title="Allowlist Sanitization Example")

settings = ObservabilitySettings(
    app_name="compliance-api",
    service="compliance",
    environment="production",
    version="1.0.0",
    metrics_enabled=True,
)

# --- Strict allowlist policy ---
# Only log what compliance requires. Everything else is dropped.
strict_allowlist_policy = SecurityPolicy(
    # Only these headers will appear in logs:
    header_allowlist=(
        "x-request-id",
        "content-type",
        "user-agent",
        "traceparent",
    ),
    # Only these event keys will appear in the log event:
    event_key_allowlist=(
        "method",
        "path",
        "status-code",
        "duration-ms",
        "error-type",
    ),
)

install_observability(
    app,
    settings,
    security_policy=strict_allowlist_policy,
    trusted_proxy_policy=TrustedProxyPolicy(enabled=True),
)


@app.get("/users/{user_id}")
def get_user(user_id: int) -> dict[str, str | int]:
    """
    In the log output for this request, you'll see only:
      "method": "GET"
      "path": "/users/42"
      "status_code": 200
      "duration_ms": 1.234
      "error_type": "ok"

    Fields like client_ip, request_body, response_body are NOT logged
    because they're not in the event_key_allowlist.
    """
    return {"user_id": user_id, "name": "Alice"}
