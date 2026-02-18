from __future__ import annotations

import hashlib
import ipaddress
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .utils import parse_csv_tuple

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


class SecurityPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    redacted_fields: tuple[str, ...] = DEFAULT_REDACTED_FIELDS
    redacted_headers: tuple[str, ...] = DEFAULT_REDACTED_HEADERS
    redaction_mode: RedactionMode = "mask"
    mask_text: str = "***"
    log_request_body: bool = False
    log_response_body: bool = False
    max_body_length: int = Field(default=256, gt=0)

    @classmethod
    def from_env(cls) -> "SecurityPolicy":
        return cls(**_SecurityPolicySettings().model_dump())


class TrustedProxyPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    trusted_cidrs: tuple[str, ...] = DEFAULT_TRUSTED_CIDRS
    honor_forwarded_headers: bool = False

    @classmethod
    def from_env(cls) -> "TrustedProxyPolicy":
        return cls(**_TrustedProxyPolicySettings().model_dump())


class _SecurityPolicySettings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    redacted_fields: str | tuple[str, ...] = Field(
        default=DEFAULT_REDACTED_FIELDS,
        validation_alias="OBS_REDACTED_FIELDS",
    )
    redacted_headers: str | tuple[str, ...] = Field(
        default=DEFAULT_REDACTED_HEADERS,
        validation_alias="OBS_REDACTED_HEADERS",
    )
    redaction_mode: RedactionMode = Field(
        default="mask",
        validation_alias="OBS_REDACTION_MODE",
    )
    mask_text: str = Field(default="***", validation_alias="OBS_MASK_TEXT")
    log_request_body: bool = Field(
        default=False,
        validation_alias="OBS_LOG_REQUEST_BODY",
    )
    log_response_body: bool = Field(
        default=False,
        validation_alias="OBS_LOG_RESPONSE_BODY",
    )
    max_body_length: int = Field(default=256, gt=0, validation_alias="OBS_MAX_BODY_LENGTH")

    @field_validator("redacted_fields", "redacted_headers", mode="before")
    @classmethod
    def _parse_tuple_values(cls, value: object, info: ValidationInfo) -> tuple[str, ...]:
        defaults = {
            "redacted_fields": DEFAULT_REDACTED_FIELDS,
            "redacted_headers": DEFAULT_REDACTED_HEADERS,
        }
        field_name = info.field_name or "redacted_fields"
        return parse_csv_tuple(value, defaults[field_name])


class _TrustedProxyPolicySettings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    enabled: bool = Field(default=True, validation_alias="OBS_TRUSTED_PROXY_ENABLED")
    trusted_cidrs: str | tuple[str, ...] = Field(
        default=DEFAULT_TRUSTED_CIDRS,
        validation_alias="OBS_TRUSTED_CIDRS",
    )
    honor_forwarded_headers: bool = Field(
        default=False,
        validation_alias="OBS_HONOR_FORWARDED_HEADERS",
    )

    @field_validator("trusted_cidrs", mode="before")
    @classmethod
    def _parse_trusted_cidrs(cls, value: object) -> tuple[str, ...]:
        return parse_csv_tuple(value, DEFAULT_TRUSTED_CIDRS)


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
