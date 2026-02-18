from __future__ import annotations

from observabilityfastapi.security import SecurityPolicy, sanitize_event


def test_sanitize_event_masks_default_sensitive_fields() -> None:
    payload = {
        "password": "super-secret",
        "token": "token-value",
        "headers": {
            "authorization": "Bearer abc",
            "x-request-id": "req-1",
        },
        "nested": {
            "client_secret": "client-secret",
        },
    }

    sanitized = sanitize_event(payload, SecurityPolicy())

    assert sanitized["password"] == "***"
    assert sanitized["token"] == "***"
    assert sanitized["headers"]["authorization"] == "***"
    assert sanitized["headers"]["x-request-id"] == "req-1"
    assert sanitized["nested"]["client_secret"] == "***"


def test_sanitize_event_drop_mode_removes_sensitive_fields() -> None:
    payload = {"password": "secret", "username": "alice"}

    sanitized = sanitize_event(payload, SecurityPolicy(redaction_mode="drop"))

    assert "password" not in sanitized
    assert sanitized["username"] == "alice"
