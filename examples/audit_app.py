"""
audit_app.py — Demonstrates Tamper-Evident Audit Logging.

Prerequisites:
    pip install "fastapi-observer[audit]"

Run this:
    export OBS_AUDIT_SECRET_KEY="my-secret-signing-key"
    uvicorn examples.audit_app:app --reload

What happens under the hood:
    Every structured JSON log exported by this application will include an HMAC-SHA256 signature
    chain.
    - `_audit_seq`: Increments monotonically for every log emitted.
    - `_audit_stream`: A unique ID denoting this exact application instance's stream.
    - `_audit_sig`: A cryptographically secure signature binding the current record, the sequence
                    number, the stream ID, and the signature of the previous record.

You can verify the output stream using the included verification tool:
    python scripts/verify_audit_chain.py path/to/logs.ndjson
"""

import logging
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel

from fastapiobserver import (
    ObservabilitySettings,
    install_observability,
)

app = FastAPI(title="Tamper-Evident Audit Example")
logger = logging.getLogger("examples.audit_app")

# In production, OBS_AUDIT_SECRET_KEY is read from the environment
settings = ObservabilitySettings(
    app_name="banking-api",
    service="vault",
    environment="production",
    audit_logging_enabled=True,
    # This environment variable should be securely injected via Vault/KMS or Kubernetes Secrets
    audit_key_env_var="OBS_AUDIT_SECRET_KEY"
)

install_observability(
    app,
    settings,
)


class TransferRequest(BaseModel):
    to_account: str
    amount: float


@app.post("/transfer")
def execute_transfer(transfer: TransferRequest) -> dict[str, Any]:
    """
    Every log here is cryptographically signed. If this log stream is sent to DataDog, 
    ElasticSearch, or a raw log file, you have mathematical proof the logs were not 
    altered, reordered, or deleted by a bad actor.
    """
    logger.info(
        "Initiating funds transfer",
        extra={
            "event": {
                "destination": transfer.to_account,
                "amount": transfer.amount,
                "status": "pending_compliance_check",
            }
        },
    )

    # ... execute business logic ...

    logger.info(
        "Funds transfer committed successfully",
        extra={
            "event": {
                "destination": transfer.to_account,
                "amount": transfer.amount,
                "status": "success",
            }
        },
    )

    return {"status": "success", "amount": transfer.amount}
