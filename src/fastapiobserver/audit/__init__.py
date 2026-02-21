"""Tamper-evident audit logging — optional HMAC-SHA256 hash chain."""
from __future__ import annotations

from .formatter import AuditChainFormatter
from .providers import AuditKeyProvider, LocalHMACProvider
from .verify import AuditVerificationResult, verify_audit_chain

__all__ = [
    "AuditChainFormatter",
    "AuditKeyProvider",
    "AuditVerificationResult",
    "LocalHMACProvider",
    "verify_audit_chain",
]
