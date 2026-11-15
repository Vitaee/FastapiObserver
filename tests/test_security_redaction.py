from __future__ import annotations

import json
import logging
from urllib.parse import parse_qsl

import pytest
from pydantic import SecretStr

from fastapiobserver import ObservabilitySettings, StructuredJsonFormatter
from fastapiobserver.middleware.events import _RequestEventBuilder, _sanitize_exception_message
from fastapiobserver.security import SecurityPolicy, sanitize_event, strip_url_credentials
from fastapiobserver.security.redaction import MAX_SANITIZE_DEPTH, redact_body


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


_ALLOWLIST = SecurityPolicy(event_key_allowlist=("method", "status_code"))


def test_an_event_key_allowlist_filters_the_event_not_the_records_own_fields() -> None:
    from fastapiobserver.security import sanitize_record

    record = {
        "timestamp": "2026-11-01T00:00:00+00:00",
        "message": "request.completed",
        "request_id": "req-1",
        "event": {"method": "GET", "path": "/orders/42", "status_code": 200, "password": "x"},
    }

    assert sanitize_record(record, _ALLOWLIST) == {
        "timestamp": "2026-11-01T00:00:00+00:00",
        "message": "request.completed",
        "request_id": "req-1",
        "event": {"method": "GET", "status_code": 200},
    }


def test_request_lines_keep_their_fields_with_an_event_key_allowlist(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from fastapiobserver import install_observability
    from fastapiobserver.logging import flush_logging

    app = FastAPI()

    @app.get("/orders/{order_id}")
    def order(order_id: int) -> dict[str, int]:
        return {"order_id": order_id}

    install_observability(
        app, ObservabilitySettings(), security_policy=_ALLOWLIST, metrics_enabled=False
    )
    TestClient(app).get("/orders/42")
    flush_logging()

    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line[:1] == "{"]
    [completed] = [line for line in lines if line.get("message") == "request.completed"]
    assert completed["request_id"]
    assert completed["event"] == {"method": "GET", "status_code": 200}


def test_otlp_attributes_keep_the_records_fields_with_an_event_key_allowlist() -> None:
    from fastapiobserver.otel.logs import _sanitize_record_custom_attributes

    record = logging.makeLogRecord(
        {
            "msg": "request.completed",
            "request_id": "req-1",
            "event": {"method": "GET", "path": "/x"},
        }
    )
    _sanitize_record_custom_attributes(record, _ALLOWLIST)

    assert record.request_id == "req-1"  # type: ignore[attr-defined]
    assert record.event == {"method": "GET"}  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (r'{"pass\u0077ord":"secret","name":"ada"}', {"password": "***", "name": "ada"}),
        (r'[{"credentials":{"pass\u0077ord":"secret"}}]', [{"credentials": {"password": "***"}}]),
        ('{"password":{"nested":"secret"}}', {"password": "***"}),
        ('{"password":["secret"]}', {"password": "***"}),
        ('{"password":"secret","password":"***"}', {"password": "***"}),
    ],
)
def test_body_redaction_decodes_json_before_redacting(body: str, expected: object) -> None:
    result = redact_body(body, SecurityPolicy())

    assert "secret" not in result
    assert json.loads(result) == expected


def test_body_redaction_decodes_form_keys_and_preserves_repeated_fields() -> None:
    body = "name=Ada+Lovelace&note=a%26b%3Dc&pass%77ord=secret&password=second&empty="

    result = redact_body(body, SecurityPolicy())

    assert parse_qsl(result, keep_blank_values=True) == [
        ("name", "Ada Lovelace"),
        ("note", "a&b=c"),
        ("password", "***"),
        ("password", "***"),
        ("empty", ""),
    ]


@pytest.mark.parametrize("body", [r'{"pass\u0077ord":"secret"}', "pass%77ord=secret"])
@pytest.mark.parametrize("mode", ["mask", "drop", "hash"])
def test_encoded_body_keys_follow_the_redaction_mode(body: str, mode: str) -> None:
    policy = SecurityPolicy(redaction_mode=mode, hash_key=SecretStr("test-key"))

    result = redact_body(body, policy)
    parsed = json.loads(result) if body.startswith("{") else dict(parse_qsl(result))

    assert "secret" not in result
    if mode == "hash":
        assert parsed["password"].startswith("hmac-sha256:")
    else:
        assert parsed["password"] == "***"


@pytest.mark.parametrize(
    "body",
    [
        r'{"pass\u0077ord":"secret',
        '{"password":"secret=unfinished',
        '\ufeff{"password":"secret"}',
        "plain secret text",
        '{"access_token": "abc.de',
    ],
)
def test_body_redaction_masks_bodies_it_cannot_safely_inspect(body: str) -> None:
    assert redact_body(body, SecurityPolicy()) == "***"


def test_body_redaction_masks_excessive_nesting() -> None:
    body = "[" * 1100 + '{"password":"secret"}' + "]" * 1100

    result = redact_body(body, SecurityPolicy())

    assert "secret" not in result
    assert "***" in result
    assert len(result) < len(body)


def test_hash_redaction_accepts_unpaired_surrogates_in_json_values() -> None:
    policy = SecurityPolicy(redaction_mode="hash", hash_key=SecretStr("test-key"))

    result = redact_body(r'{"password":"\ud800"}', policy)

    assert json.loads(result)["password"].startswith("hmac-sha256:")
    assert "\\ud800" not in result


def test_event_allowlist_does_not_remove_body_fields() -> None:
    policy = SecurityPolicy(event_key_allowlist=("method",))

    result = redact_body(r'{"name":"ada","pass\u0077ord":"secret"}', policy)

    assert json.loads(result) == {"name": "ada", "password": "***"}


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("username=ada&password=hunter2&x=1", "username=ada&password=***&x=1"),
        (
            '{"user":{"secret":7},"items":[{"token":"t"}]}',
            '{"user":{"secret":"***"},"items":[{"token":"***"}]}',
        ),
        ('{"note":"no secrets, 10:00"}', '{"note":"no secrets, 10:00"}'),
    ],
)
def test_body_redaction_keeps_the_original_layout(body: str, expected: str) -> None:
    assert redact_body(body, SecurityPolicy()) == expected


def test_sanitize_event_masks_data_beyond_depth_limit() -> None:
    deep_dict: dict = {"password": "secret"}
    deep_list: list = [{"password": "secret"}]
    for _ in range(MAX_SANITIZE_DEPTH + 20):
        deep_dict = {"nested": deep_dict}
        deep_list = [deep_list]

    result = sanitize_event({"dict": deep_dict, "list": deep_list}, SecurityPolicy())

    assert "secret" not in repr(result)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Cannot connect to postgres://admin:s3cret@db.example.com:5432/prod",
            "Cannot connect to postgres://***:***@db.example.com:5432/prod",
        ),
        (
            "DB: postgres://u1:p1@host1/db Cache: redis://u2:p2@host2/0",
            "DB: postgres://***:***@host1/db Cache: redis://***:***@host2/0",
        ),
        ("redis://:s3cret@cache:6379/0", "redis://***:***@cache:6379/0"),
        ("Connection refused on port 8080", "Connection refused on port 8080"),
        (
            'GET http://api:8080/users failed\n    @app.get("/users")',
            'GET http://api:8080/users failed\n    @app.get("/users")',
        ),
    ],
)
def test_url_credentials_are_stripped_from_text(text: str, expected: str) -> None:
    assert strip_url_credentials(text) == expected


def test_exception_messages_are_stripped_and_truncated() -> None:
    assert len(_sanitize_exception_message("x" * 1000, max_length=256)) == 256
    event = _RequestEventBuilder(SecurityPolicy()).build(
        method="GET",
        path="/test",
        status_code=500,
        duration_seconds=0.1,
        client_ip=None,
        request_body=None,
        response_body=None,
        error_type="server_error",
        exception=ConnectionError("Failed: postgres://admin:secret123@db.prod.internal:5432/mydb"),
    )
    assert event["exception_message"] == "Failed: postgres://***:***@db.prod.internal:5432/mydb"


def test_formatter_strips_credentials_from_error() -> None:
    formatter = StructuredJsonFormatter(
        ObservabilitySettings(app_name="t", service="t", environment="t")
    )
    try:
        raise ConnectionError("could not connect to postgresql://admin:hunter2@db/app")
    except ConnectionError as error:
        record = logging.LogRecord(
            "t", logging.ERROR, __file__, 1, "db.failed", None,
            (type(error), error, error.__traceback__),
        )

    line = formatter.format(record)

    assert "hunter2" not in line
    error_payload = json.loads(line)["error"]
    assert error_payload["message"] == "could not connect to postgresql://***:***@db/app"
    assert "postgresql://***:***@db/app" in error_payload["stacktrace"]
