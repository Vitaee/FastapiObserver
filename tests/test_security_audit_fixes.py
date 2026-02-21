"""Tests for security audit fixes.

Covers:
- GDPR preset redacts `client_ip` event key
- Exception message credential sanitization
- Recursion depth limit in sanitize_event
- Forwarded-headers startup warning log
"""
from __future__ import annotations

import logging

import pytest

from fastapiobserver.middleware.events import (
    _RequestEventBuilder,
    _sanitize_exception_message,
)
from fastapiobserver.security import SecurityPolicy, sanitize_event
from fastapiobserver.security.redaction import MAX_SANITIZE_DEPTH


# ---------------------------------------------------------------------------
# P0: GDPR preset redacts `client_ip`
# ---------------------------------------------------------------------------

def test_gdpr_preset_redacts_client_ip() -> None:
    policy = SecurityPolicy.from_preset("gdpr")
    event = {"client_ip": "203.0.113.42", "method": "GET"}
    sanitized = sanitize_event(event, policy)
    assert sanitized["client_ip"].startswith("sha256:")
    assert sanitized["method"] == "GET"


def test_gdpr_preset_redacts_client_ip_inside_nested_dict() -> None:
    policy = SecurityPolicy.from_preset("gdpr")
    event = {"nested": {"client_ip": "10.0.0.1"}}
    sanitized = sanitize_event(event, policy)
    assert sanitized["nested"]["client_ip"].startswith("sha256:")


# ---------------------------------------------------------------------------
# P0: Exception message credential sanitization
# ---------------------------------------------------------------------------

class TestSanitizeExceptionMessage:
    def test_strips_basic_credential_url(self) -> None:
        msg = "Cannot connect to postgres://admin:s3cret@db.example.com:5432/prod"
        result = _sanitize_exception_message(msg)
        assert "s3cret" not in result
        assert "://***:***@" in result
        assert "db.example.com:5432/prod" in result

    def test_strips_redis_credential_url(self) -> None:
        msg = "redis://default:my_password@redis.local:6379/0"
        result = _sanitize_exception_message(msg)
        assert "my_password" not in result
        assert "://***:***@" in result

    def test_strips_multiple_credentials(self) -> None:
        msg = (
            "DB: postgres://u1:p1@host1/db "
            "Cache: redis://u2:p2@host2/0"
        )
        result = _sanitize_exception_message(msg)
        assert "p1" not in result
        assert "p2" not in result
        assert result.count("://***:***@") == 2

    def test_truncates_long_messages(self) -> None:
        msg = "x" * 1000
        result = _sanitize_exception_message(msg, max_length=256)
        assert len(result) == 256

    def test_preserves_safe_messages(self) -> None:
        msg = "Connection refused on port 8080"
        assert _sanitize_exception_message(msg) == msg

    def test_event_builder_sanitizes_exception(self) -> None:
        builder = _RequestEventBuilder(SecurityPolicy())
        error = ConnectionError(
            "Failed: postgres://admin:secret123@db.prod.internal:5432/mydb"
        )
        event = builder.build(
            method="GET",
            path="/test",
            status_code=500,
            duration_seconds=0.1,
            client_ip=None,
            request_body=None,
            response_body=None,
            error_type="server_error",
            exception=error,
        )
        assert "secret123" not in event["exception_message"]
        assert "://***:***@" in event["exception_message"]


# ---------------------------------------------------------------------------
# P1: Recursion depth limit
# ---------------------------------------------------------------------------

def test_sanitize_event_handles_deeply_nested_payload() -> None:
    """Ensure sanitize_event doesn't RecursionError on extreme nesting."""
    depth = MAX_SANITIZE_DEPTH + 20  # well beyond the limit
    data: dict = {"leaf": "value"}
    for _ in range(depth):
        data = {"nested": data}
    # Must not raise RecursionError
    result = sanitize_event(data, SecurityPolicy())
    assert isinstance(result, dict)


def test_sanitize_event_still_redacts_within_depth_limit() -> None:
    """Normal nesting (within limit) still redacts correctly."""
    data: dict = {"password": "secret"}
    for _ in range(5):
        data = {"nested": data}
    result = sanitize_event(data, SecurityPolicy())
    # Walk to the innermost level
    inner = result
    for _ in range(5):
        inner = inner["nested"]
    assert inner["password"] == "***"


# ---------------------------------------------------------------------------
# P1: Forwarded-headers startup warning
# ---------------------------------------------------------------------------

def test_ip_resolver_warns_when_forwarded_headers_enabled(caplog: pytest.LogCaptureFixture) -> None:
    from fastapiobserver.middleware.ip import _IpResolver
    from fastapiobserver.security import TrustedProxyPolicy

    policy = TrustedProxyPolicy(
        enabled=True,
        honor_forwarded_headers=True,
    )
    with caplog.at_level(logging.WARNING, logger="fastapiobserver.security"):
        _IpResolver(policy)

    assert any(
        "security.forwarded_headers.enabled" in record.message
        for record in caplog.records
    )


def test_ip_resolver_no_warning_when_forwarded_headers_disabled(caplog: pytest.LogCaptureFixture) -> None:
    from fastapiobserver.middleware.ip import _IpResolver
    from fastapiobserver.security import TrustedProxyPolicy

    policy = TrustedProxyPolicy(enabled=True, honor_forwarded_headers=False)
    with caplog.at_level(logging.WARNING, logger="fastapiobserver.security"):
        _IpResolver(policy)

    assert not any(
        "X-Forwarded-For trust is enabled" in record.message
        for record in caplog.records
    )
