"""Key provider protocol and built-in implementations for audit logging."""
from __future__ import annotations

import os
from typing import Protocol, runtime_checkable


@runtime_checkable
class AuditKeyProvider(Protocol):
    """Supplies the HMAC signing key for tamper-evident audit chains.

    Implement this protocol to integrate custom key management systems
    (AWS KMS, Google Cloud KMS, HashiCorp Vault, etc.).
    """

    def get_key(self) -> bytes: ...


class LocalHMACProvider:
    """Reads the HMAC key from an environment variable.

    This is the simplest provider — suitable for single-machine deployments
    or environments where secrets are injected via env (e.g. Kubernetes).
    """

    def __init__(self, env_var: str = "OBS_AUDIT_SECRET_KEY") -> None:
        self._env_var = env_var
        # Validate eagerly so misconfiguration fails at startup, not mid-flight.
        _ = self.get_key()

    def get_key(self) -> bytes:
        raw = os.environ.get(self._env_var)
        if not raw:
            raise ValueError(
                f"Audit logging requires a signing key. "
                f"Set the {self._env_var!r} environment variable."
            )
            
        raw = raw.strip()
        # High entropy hex-encoded key (e.g. 32 bytes = 64 hex chars)
        if len(raw) == 64:
            try:
                return bytes.fromhex(raw)
            except ValueError:
                pass
                
        # Fallback to UTF-8 for simpler local testing/legacy
        return raw.encode("utf-8")


__all__ = ["AuditKeyProvider", "LocalHMACProvider"]
