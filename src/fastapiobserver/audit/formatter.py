"""HMAC-SHA256 hash-chain formatter for tamper-evident audit logging."""
from __future__ import annotations

import hashlib
import hmac
import logging
import threading
from typing import Any

from .providers import AuditKeyProvider

# Genesis block: 32 zero bytes — deterministic, verifiable starting point.
_GENESIS_SIG = b"\x00" * 32


class AuditChainFormatter(logging.Formatter):
    """Decorates a delegate formatter with an HMAC-SHA256 hash chain.

    Every ``format()`` call:
    1. Delegates to the wrapped formatter to produce the JSON string.
    2. Computes ``HMAC-SHA256(key, "{stream_id}:{seq}:{prev_sig_hex}:{json}")``
    3. Injects ``_audit_stream``, ``_audit_seq``, and ``_audit_sig`` into the JSON output.

    The chain guarantees that:
    - **Tampered** records produce a different signature.
    - **Deleted** records break the chain continuity.
    - **Reordered** records break the sequence + prev_sig linkage.

    Thread-safety: uses a lock around ``_seq`` / ``_prev_sig`` mutation.
    """

    def __init__(
        self,
        delegate: logging.Formatter,
        key_provider: AuditKeyProvider,
    ) -> None:
        super().__init__()
        self._delegate = delegate
        self._key_provider = key_provider
        self._seq: int = 0
        self._prev_sig: bytes = _GENESIS_SIG
        self._lock = threading.Lock()
        
        # A unique stream identifier to prevent sequence collisions 
        # when multiple instances write to the same aggregated sink.
        import uuid
        self._stream_id = uuid.uuid4().hex

    def format(self, record: logging.LogRecord) -> str:
        # Strip trailing newlines/whitespace to guarantee exact string surgery reconstruction
        json_str = self._delegate.format(record).strip()

        with self._lock:
            self._seq += 1
            seq = self._seq
            prev_sig_hex = self._prev_sig.hex()
            stream_id = self._stream_id

            # The signed payload binds: stream scope + sequence number + chain link + content.
            sign_payload = f"{stream_id}:{seq}:{prev_sig_hex}:{json_str}"
            sig = hmac.new(
                self._key_provider.get_key(),
                sign_payload.encode("utf-8"),
                hashlib.sha256,
            ).digest()
            self._prev_sig = sig

        return _inject_audit_fields(json_str, stream_id, seq, sig.hex())

    def formatException(self, ei: Any) -> str:  # noqa: N802
        return self._delegate.formatException(ei)

    def formatStack(self, stack_info: str) -> str:  # noqa: N802
        return self._delegate.formatStack(stack_info)


def _inject_audit_fields(json_str: str, stream_id: str, seq: int, sig_hex: str) -> str:
    """Append audit fields backward into a JSON string.

    Uses string surgery (replaces the closing ``}``) rather than
    parse → inject → re-serialize, so the original delegate output
    bytes are preserved exactly as signed.
    """
    # Find the last '}' — the JSON object close
    idx = json_str.rfind("}")
    if idx == -1:
        # Fallback: not valid JSON, just append.
        return json_str
    audit_suffix = (
        f', "_audit_stream": "{stream_id}", '
        f'"_audit_seq": {seq}, "_audit_sig": "{sig_hex}"}}'
    )
    return json_str[:idx] + audit_suffix


__all__ = ["AuditChainFormatter"]
