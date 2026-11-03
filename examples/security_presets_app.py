"""PCI field redaction with opt-in JSON body capture.

Run: uvicorn examples.security_presets_app:app --reload
Try POST /payments with {"amount":100,"card_number":"4111111111111111","cvv":"123"}.
Card fields are masked in the JSON request log. Binary uploads are not captured.
"""

from fastapi import FastAPI, Request

from fastapiobserver import SecurityPolicy, install_observability

app = FastAPI(title="Security Presets Example")
# Presets disable body capture by default. This example explicitly opts in for JSON.
policy = SecurityPolicy.from_preset("pci").model_copy(
    update={
        "log_request_body": True,
        "body_capture_media_types": ("application/json",),
    }
)
install_observability(app, security_policy=policy)


@app.post("/payments")
async def create_payment(request: Request) -> dict[str, str]:
    body = await request.json()
    return {"status": "accepted", "amount": str(body.get("amount", 0))}


@app.post("/upload")
async def upload_file(request: Request) -> dict[str, int]:
    return {"bytes_received": len(await request.body())}
