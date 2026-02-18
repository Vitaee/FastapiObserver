from __future__ import annotations

import hashlib
import ipaddress
import os
from dataclasses import dataclass
from typing import Any, Literal, Mapping, cast

DEFAULT_REDACTED_FIELDS = (
    "password",
    "passwd",
    "token",
    "secret",
    "authorization",
    "cookie",
    "set-cookie",
    "api-key",
    "apikey",
    "access_token",
    "refresh_token",
    "client_secret",
)

DEFAULT_REDACTED_HEADERS = (
    "authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
)

DEFAULT_TRUSTED_CIDRS = (
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "127.0.0.1/32",
    "::1/128",
)

_DROP = object()
RedactionMode = Literal["mask", "hash", "drop"]


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_tuple(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    value = os.getenv(name)
    if value is None:
        return default
    items = tuple(item.strip() for item in value.split(",") if item.strip())
    return items or default


@dataclass(frozen=True)
class SecurityPolicy:
    redacted_fields: tuple[str, ...] = DEFAULT_REDACTED_FIELDS
    redacted_headers: tuple[str, ...] = DEFAULT_REDACTED_HEADERS
    redaction_mode: RedactionMode = "mask"
    mask_text: str = "***"
    log_request_body: bool = False
    log_response_body: bool = False
    max_body_length: int = 256

    def __post_init__(self) -> None:
        if self.redaction_mode not in {"mask", "hash", "drop"}:
            raise ValueError(f"Invalid redaction_mode: {self.redaction_mode}")
        if self.max_body_length <= 0:
            raise ValueError("max_body_length must be > 0")

    @classmethod
    def from_env(cls) -> "SecurityPolicy":
        max_body_length_env = os.getenv("OBS_MAX_BODY_LENGTH", "256")
        try:
            max_body_length = int(max_body_length_env)
        except ValueError:
            max_body_length = 256

        raw_redaction_mode = os.getenv("OBS_REDACTION_MODE", "mask").strip().lower()
        if raw_redaction_mode not in {"mask", "hash", "drop"}:
            raw_redaction_mode = "mask"
        redaction_mode = cast(RedactionMode, raw_redaction_mode)

        return cls(
            redacted_fields=_env_tuple("OBS_REDACTED_FIELDS", DEFAULT_REDACTED_FIELDS),
            redacted_headers=_env_tuple(
                "OBS_REDACTED_HEADERS", DEFAULT_REDACTED_HEADERS
            ),
            redaction_mode=redaction_mode,
            mask_text=os.getenv("OBS_MASK_TEXT", "***"),
            log_request_body=_env_bool("OBS_LOG_REQUEST_BODY", False),
            log_response_body=_env_bool("OBS_LOG_RESPONSE_BODY", False),
            max_body_length=max_body_length,
        )


@dataclass(frozen=True)
class TrustedProxyPolicy:
    enabled: bool = True
    trusted_cidrs: tuple[str, ...] = DEFAULT_TRUSTED_CIDRS
    honor_forwarded_headers: bool = False

    @classmethod
    def from_env(cls) -> "TrustedProxyPolicy":
        return cls(
            enabled=_env_bool("OBS_TRUSTED_PROXY_ENABLED", True),
            trusted_cidrs=_env_tuple("OBS_TRUSTED_CIDRS", DEFAULT_TRUSTED_CIDRS),
            honor_forwarded_headers=_env_bool(
                "OBS_HONOR_FORWARDED_HEADERS", False
            ),
        )


def _normalize_key(key: str) -> str:
    return key.strip().lower().replace("_", "-")


def _redact_value(value: Any, policy: SecurityPolicy) -> Any:
    if policy.redaction_mode == "drop":
        return _DROP
    if policy.redaction_mode == "hash":
        digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()
        return f"sha256:{digest}"
    return policy.mask_text


def sanitize_event(data: dict[str, Any], policy: SecurityPolicy) -> dict[str, Any]:
    sensitive_fields = {_normalize_key(item) for item in policy.redacted_fields}
    sensitive_headers = {_normalize_key(item) for item in policy.redacted_headers}
    return _sanitize_mapping(data, policy, sensitive_fields, sensitive_headers, False)


def _sanitize_mapping(
    data: Mapping[str, Any],
    policy: SecurityPolicy,
    sensitive_fields: set[str],
    sensitive_headers: set[str],
    parent_is_headers: bool,
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in data.items():
        normalized_key = _normalize_key(str(key))
        should_redact = normalized_key in sensitive_fields or (
            parent_is_headers and normalized_key in sensitive_headers
        )

        if should_redact:
            redacted = _redact_value(value, policy)
            if redacted is not _DROP:
                out[str(key)] = redacted
            continue

        out[str(key)] = _sanitize_value(
            value,
            policy,
            sensitive_fields,
            sensitive_headers,
            parent_is_headers=normalized_key == "headers",
        )
    return out


def _sanitize_value(
    value: Any,
    policy: SecurityPolicy,
    sensitive_fields: set[str],
    sensitive_headers: set[str],
    parent_is_headers: bool,
) -> Any:
    if isinstance(value, Mapping):
        string_mapping = {str(k): v for k, v in value.items()}
        return _sanitize_mapping(
            string_mapping,
            policy,
            sensitive_fields,
            sensitive_headers,
            parent_is_headers,
        )
    if isinstance(value, list):
        return [
            _sanitize_value(
                item,
                policy,
                sensitive_fields,
                sensitive_headers,
                parent_is_headers=False,
            )
            for item in value
        ]
    if isinstance(value, tuple):
        return tuple(
            _sanitize_value(
                item,
                policy,
                sensitive_fields,
                sensitive_headers,
                parent_is_headers=False,
            )
            for item in value
        )
    return value


def is_trusted_client_ip(client_ip: str | None, policy: TrustedProxyPolicy) -> bool:
    if not policy.enabled:
        return True
    if not client_ip:
        return False
    try:
        parsed_ip = ipaddress.ip_address(client_ip)
    except ValueError:
        return False

    for cidr in policy.trusted_cidrs:
        try:
            network = ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            continue
        if parsed_ip in network:
            return True
    return False


def resolve_client_ip(
    client_ip: str | None,
    headers: Mapping[str, str],
    policy: TrustedProxyPolicy,
) -> str | None:
    if not policy.honor_forwarded_headers:
        return client_ip
    if not is_trusted_client_ip(client_ip, policy):
        return client_ip
    forwarded_for = headers.get("x-forwarded-for")
    if not forwarded_for:
        return client_ip
    first_hop = forwarded_for.split(",")[0].strip()
    return first_hop or client_ip
