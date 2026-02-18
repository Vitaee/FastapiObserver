"""
security_presets_app.py — Demonstrates security presets and body capture.

Run this:
    uvicorn examples.security_presets_app:app --reload

Then try:
    # 1) POST some JSON — body will be captured and logged:
    curl -X POST http://localhost:8000/payments \
      -H "Content-Type: application/json" \
      -d '{"amount": 100, "card_number": "4111111111111111", "cvv": "123"}'

    # 2) POST binary data — body will NOT be captured (media-type not in allowlist):
    curl -X POST http://localhost:8000/upload \
      -H "Content-Type: application/octet-stream" \
      --data-binary @somefile.bin

What happens under the hood with security presets:

    SecurityPolicy.from_preset("pci")  creates a policy that:
      - Masks (replaces with "***") all default sensitive fields PLUS
        PCI-specific fields: card_number, cvv, pan, expiry, track_data
      - Disables body logging by default
      - Uses "mask" redaction mode

    You can layer overrides on top of a preset:
      SecurityPolicy.from_preset("pci").model_copy(update={"log_request_body": True})

    The three built-in presets are:
      "strict" → drops sensitive fields entirely, only allows 4 safe headers
      "pci"    → masks cardholder data fields (for payment processing)
      "gdpr"   → hashes PII fields with SHA-256 (for audit trail compliance)

    You can also set presets via environment variable:
      export OBS_REDACTION_PRESET=gdpr

What happens with body_capture_media_types:

    When set to ("application/json",), the middleware checks the Content-Type
    header of each request. Only requests with Content-Type starting with
    "application/json" will have their body captured in the log.

    This prevents accidentally logging binary uploads, multipart form data,
    or other non-text content types.

    The check also works for response bodies — the middleware reads the
    response Content-Type header from http.response.start and dynamically
    enables/disables capture.
"""

from fastapi import FastAPI, Request

from fastapiobserver import (
    ObservabilitySettings,
    SecurityPolicy,
    TrustedProxyPolicy,
    install_observability,
)

app = FastAPI(title="Security Presets Example")

settings = ObservabilitySettings(
    app_name="payments-api",
    service="payments",
    environment="production",
    version="1.0.0",
    metrics_enabled=True,
)

# --- Using a PCI preset with body capture ---
# Start from the "pci" preset, then enable request body logging
# but restrict it to JSON content only.
pci_policy = SecurityPolicy.from_preset("pci").model_copy(
    update={
        "log_request_body": True,
        # Only capture JSON bodies — binary uploads, multipart forms,
        # and other content types will be silently skipped.
        "body_capture_media_types": ("application/json",),
    }
)

install_observability(
    app,
    settings,
    security_policy=pci_policy,
    trusted_proxy_policy=TrustedProxyPolicy(enabled=True),
)


@app.post("/payments")
async def create_payment(request: Request) -> dict[str, str]:
    """
    POST a JSON payment.

    In the log output, you'll see:
      - "card_number": "***"   (masked by PCI preset)
      - "cvv": "***"           (masked by PCI preset)
      - "amount": 100          (not sensitive, logged as-is)
      - "request_body": '{"amount": 100, "card_number": "***", "cvv": "***"}'

    Wait — the request_body field shows the RAW body before redaction.
    The redaction happens on the event dict keys, not inside the raw body string.
    For PCI compliance, you should keep log_request_body=False in production
    and use structured event fields instead.
    """
    body = await request.json()
    return {"status": "accepted", "amount": str(body.get("amount", 0))}


@app.post("/upload")
async def upload_file(request: Request) -> dict[str, int]:
    """
    POST binary data. Because content-type is not in the allowlist,
    the body will NOT appear in logs, even though log_request_body=True.
    """
    data = await request.body()
    return {"bytes_received": len(data)}
