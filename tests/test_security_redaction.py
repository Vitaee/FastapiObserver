from __future__ import annotations

from fastapiobserver.security import SecurityPolicy, sanitize_event


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


def test_header_allowlist_drops_unlisted_headers() -> None:
    payload = {
        "headers": {
            "authorization": "Bearer abc",
            "x-request-id": "req-123",
            "user-agent": "pytest",
        }
    }
    policy = SecurityPolicy(header_allowlist=("x-request-id",))

    sanitized = sanitize_event(payload, policy)

    assert sanitized["headers"] == {"x-request-id": "req-123"}


def test_event_key_allowlist_drops_unlisted_keys() -> None:
    payload = {
        "method": "GET",
        "path": "/orders",
        "status_code": 200,
        "client_ip": "127.0.0.1",
    }
    policy = SecurityPolicy(event_key_allowlist=("method", "status_code"))

    sanitized = sanitize_event(payload, policy)

    assert sanitized == {"method": "GET", "status_code": 200}


def test_allowlist_combined_with_redaction() -> None:
    payload = {
        "headers": {
            "authorization": "Bearer abc",
            "x-api-key": "secret-key",
            "x-request-id": "req-999",
        }
    }
    policy = SecurityPolicy(
        header_allowlist=("authorization", "x-request-id"),
        redacted_headers=("authorization",),
    )

    sanitized = sanitize_event(payload, policy)

    assert sanitized["headers"]["authorization"] == "***"
    assert sanitized["headers"]["x-request-id"] == "req-999"
    assert "x-api-key" not in sanitized["headers"]
