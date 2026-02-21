"""Tests for tamper-evident audit logging feature.

Covers: hash chain correctness, verification, key providers, integration,
and tamper / deletion / reorder detection.
"""
from __future__ import annotations

import json
import logging
import os

import pytest

from fastapiobserver import ObservabilitySettings, SecurityPolicy
from fastapiobserver.audit import (
    AuditChainFormatter,
    AuditKeyProvider,
    AuditVerificationResult,
    LocalHMACProvider,
    verify_audit_chain,
)
from fastapiobserver.logging import StructuredJsonFormatter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TEST_KEY = b"test-audit-secret-key-12345"


class _StaticKeyProvider:
    """Simple provider that returns a fixed key."""

    def get_key(self) -> bytes:
        return _TEST_KEY


def _make_formatter() -> AuditChainFormatter:
    settings = ObservabilitySettings(
        app_name="test", service="test", environment="test",
    )
    delegate = StructuredJsonFormatter(settings, security_policy=SecurityPolicy())
    return AuditChainFormatter(delegate=delegate, key_provider=_StaticKeyProvider())


def _make_record(msg: str = "hello") -> logging.LogRecord:
    return logging.LogRecord(
        name="test.logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=None,
    )


def _format_n_records(formatter: AuditChainFormatter, n: int) -> list[str]:
    return [formatter.format(_make_record(f"msg-{i}")) for i in range(n)]


# ---------------------------------------------------------------------------
# Core hash chain
# ---------------------------------------------------------------------------


class TestAuditChainFormatter:
    def test_appends_seq_and_sig(self) -> None:
        formatter = _make_formatter()
        output = formatter.format(_make_record())
        payload = json.loads(output)
        assert "_audit_seq" in payload
        assert "_audit_sig" in payload
        assert payload["_audit_seq"] == 1

    def test_seq_increments_monotonically(self) -> None:
        formatter = _make_formatter()
        records = _format_n_records(formatter, 5)
        seqs = [json.loads(r)["_audit_seq"] for r in records]
        assert seqs == [1, 2, 3, 4, 5]

    def test_chain_is_verifiable(self) -> None:
        formatter = _make_formatter()
        records = _format_n_records(formatter, 10)
        result = verify_audit_chain(records, _TEST_KEY)
        assert result.valid
        assert result.total_records == 10

    def test_genesis_block_uses_zero_bytes_prev_sig(self) -> None:
        import hashlib
        import hmac as hmac_mod

        formatter = _make_formatter()
        output = formatter.format(_make_record("genesis-test"))
        payload = json.loads(output)
        
        stream_id = payload["_audit_stream"]

        # Reconstruct original JSON (strip audit suffix)
        from fastapiobserver.audit.verify import _AUDIT_SUFFIX_RE
        original_json = _AUDIT_SUFFIX_RE.sub("}", output)

        # Genesis prev_sig is 32 zero bytes
        genesis_prev = b"\x00" * 32
        sign_payload = f"{stream_id}:1:{genesis_prev.hex()}:{original_json}"
        expected_sig = hmac_mod.new(
            _TEST_KEY, sign_payload.encode("utf-8"), hashlib.sha256,
        ).hexdigest()

        assert payload["_audit_seq"] == 1
        assert payload["_audit_sig"] == expected_sig


# ---------------------------------------------------------------------------
# Tamper detection
# ---------------------------------------------------------------------------


class TestTamperDetection:
    def test_tampered_record_fails(self) -> None:
        formatter = _make_formatter()
        records = _format_n_records(formatter, 5)
        # Tamper with record #3 (index 2)
        tampered = json.loads(records[2])
        tampered["message"] = "TAMPERED"
        records[2] = json.dumps(tampered)
        result = verify_audit_chain(records, _TEST_KEY)
        assert not result.valid
        assert result.failed_at_seq == 3

    def test_deleted_record_fails(self) -> None:
        formatter = _make_formatter()
        records = _format_n_records(formatter, 5)
        del records[2]  # Delete record #3
        result = verify_audit_chain(records, _TEST_KEY)
        assert not result.valid

    def test_reordered_records_fail(self) -> None:
        formatter = _make_formatter()
        records = _format_n_records(formatter, 5)
        records[1], records[3] = records[3], records[1]  # Swap
        result = verify_audit_chain(records, _TEST_KEY)
        assert not result.valid

    def test_wrong_key_fails(self) -> None:
        formatter = _make_formatter()
        records = _format_n_records(formatter, 3)
        result = verify_audit_chain(records, b"wrong-key")
        assert not result.valid
        assert result.failed_at_seq == 1


# ---------------------------------------------------------------------------
# Verification edge cases
# ---------------------------------------------------------------------------


class TestVerification:
    def test_empty_input_is_valid(self) -> None:
        result = verify_audit_chain([], _TEST_KEY)
        assert result.valid
        assert result.total_records == 0

    def test_blank_lines_are_skipped(self) -> None:
        formatter = _make_formatter()
        records = _format_n_records(formatter, 2)
        lines = [records[0], "", "  ", records[1]]
        result = verify_audit_chain(lines, _TEST_KEY)
        assert result.valid
        assert result.total_records == 2

    def test_malformed_json_fails(self) -> None:
        result = verify_audit_chain(["not-json"], _TEST_KEY)
        assert not result.valid
        assert "Malformed JSON" in (result.error or "")

    def test_missing_audit_fields_fails(self) -> None:
        result = verify_audit_chain(['{"message": "no sig"}'], _TEST_KEY)
        assert not result.valid
        assert "Missing _audit_stream" in (result.error or "")

    def test_newline_stripping_preserves_surgery(self) -> None:
        import hashlib
        import hmac as hmac_mod

        # Manually create a formatter that outputs a trailing newline
        formatter = _make_formatter()
        original_format = formatter._delegate.format

        def malformed_format(record: logging.LogRecord) -> str:
            return original_format(record) + "   \n\n\t "

        formatter._delegate.format = malformed_format  # type: ignore

        output = formatter.format(_make_record("surgery-test"))
        payload = json.loads(output)
        
        # The surgery should have cleanly stripped the newlines before signing,
        # so verification should succeed.
        result = verify_audit_chain([output], _TEST_KEY)
        assert result.valid

    def test_multi_stream_interleaved_records(self) -> None:
        formatter_a = _make_formatter()
        formatter_b = _make_formatter()

        records_a = _format_n_records(formatter_a, 5)
        records_b = _format_n_records(formatter_b, 5)

        # Interleave records: A1, B1, A2, B2, A3...
        interleaved = []
        for i in range(5):
            interleaved.append(records_a[i])
            interleaved.append(records_b[i])

        result = verify_audit_chain(interleaved, _TEST_KEY)
        assert result.valid
        assert result.total_records == 10


# ---------------------------------------------------------------------------
# Key providers
# ---------------------------------------------------------------------------


class TestLocalHMACProvider:
    def test_reads_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OBS_AUDIT_SECRET_KEY", "my-secret")
        provider = LocalHMACProvider()
        assert provider.get_key() == b"my-secret"

    def test_custom_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MY_CUSTOM_KEY", "custom-secret")
        provider = LocalHMACProvider(env_var="MY_CUSTOM_KEY")
        assert provider.get_key() == b"custom-secret"

    def test_hex_encoded_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import os
        hex_key = os.urandom(32).hex()
        monkeypatch.setenv("OBS_AUDIT_SECRET_KEY", hex_key)
        provider = LocalHMACProvider()
        assert provider.get_key() == bytes.fromhex(hex_key)

    def test_missing_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OBS_AUDIT_SECRET_KEY", raising=False)
        with pytest.raises(ValueError, match="OBS_AUDIT_SECRET_KEY"):
            LocalHMACProvider()

    def test_satisfies_protocol(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OBS_AUDIT_SECRET_KEY", "test")
        provider = LocalHMACProvider()
        assert isinstance(provider, AuditKeyProvider)


# ---------------------------------------------------------------------------
# Custom key provider protocol
# ---------------------------------------------------------------------------


class TestCustomKeyProvider:
    def test_custom_provider_works(self) -> None:
        class VaultKeyProvider:
            def get_key(self) -> bytes:
                return b"vault-managed-key"

        settings = ObservabilitySettings(
            app_name="test", service="test", environment="test",
        )
        delegate = StructuredJsonFormatter(settings, security_policy=SecurityPolicy())
        formatter = AuditChainFormatter(
            delegate=delegate, key_provider=VaultKeyProvider(),
        )
        records = [formatter.format(_make_record(f"msg-{i}")) for i in range(3)]
        result = verify_audit_chain(records, b"vault-managed-key")
        assert result.valid


# ---------------------------------------------------------------------------
# Feature disabled by default
# ---------------------------------------------------------------------------


def test_audit_disabled_produces_no_audit_fields() -> None:
    settings = ObservabilitySettings(
        app_name="test", service="test", environment="test",
    )
    formatter = StructuredJsonFormatter(settings, security_policy=SecurityPolicy())
    output = formatter.format(_make_record())
    payload = json.loads(output)
    assert "_audit_seq" not in payload
    assert "_audit_sig" not in payload
