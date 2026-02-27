# Tamper-evident audit logging

For regulated industries (Fintech, Healthcare, SOC 2) where you must prove logs were not altered, deleted, or reordered:

```bash
pip install "fastapi-observer[audit]"
export OBS_AUDIT_SECRET_KEY="your-signing-secret"
export OBS_AUDIT_LOGGING_ENABLED="true"
```

```python
install_observability(app, settings)
# Every JSON log now contains _audit_seq and _audit_sig fields
```

Each log record is chained via HMAC-SHA256: record B's signature includes the signature of record A. Breaking any link (tamper, delete, reorder) invalidates the chain.

| Variable | Default | Description |
|---|---|---|
| `OBS_AUDIT_LOGGING_ENABLED` | `false` | Enable HMAC-SHA256 hash chain |
| `OBS_AUDIT_KEY_ENV_VAR` | `OBS_AUDIT_SECRET_KEY` | Name of env var containing the signing key |
| `OBS_AUDIT_SECRET_KEY` | - | The HMAC signing key (read by `LocalHMACProvider`) |

Custom key provider (e.g. KMS / Vault):

```python
from fastapiobserver import AuditKeyProvider

class VaultKeyProvider:
    def get_key(self) -> bytes:
        return vault_client.get_secret("audit-signing-key").encode()

install_observability(app, settings, audit_key_provider=VaultKeyProvider())
```

Verify logs with the CLI:

```bash
export OBS_AUDIT_SECRET_KEY="your-signing-secret"
python scripts/verify_audit_chain.py exported_logs.ndjson
# PASS — 1042 records verified, chain intact.
```

