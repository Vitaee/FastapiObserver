from __future__ import annotations

import pytest

import fastapiobserver
from fastapiobserver.security import SecurityPolicy, sanitize_event


def test_strict_preset_drops_all_sensitive_fields() -> None:
    policy = SecurityPolicy.from_preset("strict")
    payload = {
        "password": "secret",
        "token": "token",
        "headers": {
            "authorization": "Bearer abc",
            "x-request-id": "req-1",
        },
    }

    sanitized = sanitize_event(payload, policy)

    assert "password" not in sanitized
    assert "token" not in sanitized
    assert sanitized["headers"] == {"x-request-id": "req-1"}


def test_pci_preset_masks_cardholder_data() -> None:
    policy = SecurityPolicy.from_preset("pci")
    payload = {
        "card_number": "4111111111111111",
        "cvv": "123",
        "pan": "4111111111111111",
    }

    sanitized = sanitize_event(payload, policy)

    assert sanitized["card_number"] == "***"
    assert sanitized["cvv"] == "***"
    assert sanitized["pan"] == "***"


def test_gdpr_preset_hashes_pii_fields() -> None:
    policy = SecurityPolicy.from_preset("gdpr")
    payload = {
        "email": "name@example.com",
        "phone": "+1234567890",
        "address": "Main street",
    }

    sanitized = sanitize_event(payload, policy)

    assert sanitized["email"].startswith("sha256:")
    assert sanitized["phone"].startswith("sha256:")
    assert sanitized["address"].startswith("sha256:")


def test_preset_can_be_overridden_by_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OBS_REDACTION_PRESET", "gdpr")
    monkeypatch.setenv("OBS_REDACTION_MODE", "mask")
    monkeypatch.setenv("OBS_REDACTED_FIELDS", "password")

    policy = SecurityPolicy.from_env()

    assert policy.redaction_mode == "mask"
    assert policy.redacted_fields == ("password",)


def test_preset_constants_are_available_in_public_api() -> None:
    assert "strict" in fastapiobserver.SECURITY_POLICY_PRESETS
    assert fastapiobserver.PCI_REDACTED_FIELDS
    assert fastapiobserver.GDPR_REDACTED_FIELDS
