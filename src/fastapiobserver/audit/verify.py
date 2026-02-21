"""Verification utilities for tamper-evident audit chains."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from typing import Iterable, Any

# Must match the genesis in formatter.py.
_GENESIS_SIG = b"\x00" * 32

# Matches the audit suffix appended by _inject_audit_fields.
_AUDIT_SUFFIX_RE = re.compile(
    r',\s*"_audit_stream":\s*"[^"]+",\s*"_audit_seq":\s*\d+,\s*"_audit_sig":\s*"[0-9a-f]+"\s*\}\s*$'
)


@dataclass(frozen=True)
class AuditVerificationResult:
    """Outcome of an audit chain verification."""

    valid: bool
    total_records: int
    failed_at_seq: int | None = None
    failed_stream_id: str | None = None
    error: str | None = None


def verify_audit_chain(
    lines: Iterable[str],
    key: bytes,
) -> AuditVerificationResult:
    """Replay an HMAC-SHA256 hash chain and verify integrity.

    Parameters
    ----------
    lines:
        Iterable of JSON strings (one per log record). Lines that are
        empty or whitespace-only are silently skipped.
    key:
        The same HMAC key that was used at signing time.

    Returns
    -------
    AuditVerificationResult
        ``valid=True`` if every record's ``_audit_sig`` matches the
        recomputed chain, ``valid=False`` otherwise.
    """
    # Track state independently per stream_id
    stream_states: dict[str, dict[str, Any]] = {}
    total = 0

    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped:
            continue

        try:
            record = json.loads(stripped)
        except json.JSONDecodeError as exc:
            return AuditVerificationResult(
                valid=False,
                total_records=total,
                error=f"Malformed JSON at record {total + 1}: {exc}",
            )

        stream_id = record.get("_audit_stream")
        seq = record.get("_audit_seq")
        sig_hex = record.get("_audit_sig")

        if stream_id is None or seq is None or sig_hex is None:
            return AuditVerificationResult(
                valid=False,
                total_records=total,
                error=f"Missing _audit_stream, _audit_seq or _audit_sig at record {total + 1}",
            )
            
        # Initialize state for new streams
        if stream_id not in stream_states:
            stream_states[stream_id] = {
                "prev_sig": _GENESIS_SIG,
                "expected_seq": 1,
            }
            
        state = stream_states[stream_id]

        if seq != state["expected_seq"]:
            return AuditVerificationResult(
                valid=False,
                total_records=total,
                failed_at_seq=seq,
                failed_stream_id=stream_id,
                error=(
                    f"Sequence discontinuity for stream {stream_id}: "
                    f"expected {state['expected_seq']}, got {seq}"
                ),
            )

        total += 1

        # Reconstruct the original JSON by stripping the audit suffix.
        # This must exactly reverse _inject_audit_fields() string surgery.
        original_json = _AUDIT_SUFFIX_RE.sub("}", stripped)

        sign_payload = f"{stream_id}:{seq}:{state['prev_sig'].hex()}:{original_json}"
        expected_sig = hmac.new(
            key,
            sign_payload.encode("utf-8"),
            hashlib.sha256,
        ).digest()

        if not hmac.compare_digest(expected_sig.hex(), sig_hex):
            return AuditVerificationResult(
                valid=False,
                total_records=total,
                failed_at_seq=seq,
                failed_stream_id=stream_id,
                error=f"Signature mismatch at stream={stream_id} seq={seq}",
            )

        state["prev_sig"] = expected_sig
        state["expected_seq"] += 1

    return AuditVerificationResult(valid=True, total_records=total)


__all__ = ["AuditVerificationResult", "verify_audit_chain"]
