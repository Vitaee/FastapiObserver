import os
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from ..utils import EnvLoadable, parse_csv
from .normalize import _normalize_key, _normalize_media_type

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

STRICT_HEADER_ALLOWLIST = (
    "x-request-id",
    "traceparent",
    "user-agent",
    "content-type",
)
PCI_REDACTED_FIELDS = (
    "card-number",
    "cvv",
    "expiry",
    "pan",
    "track-data",
)
GDPR_REDACTED_FIELDS = (
    "email",
    "phone",
    "address",
    "date-of-birth",
    "ssn",
    "ip-address",
    "client-ip",
    "first-name",
    "last-name",
    "full-name",
)
SECURITY_POLICY_PRESETS: dict[str, dict[str, Any]] = {
    "strict": {
        "redaction_mode": "drop",
        "log_request_body": False,
        "log_response_body": False,
        "header_allowlist": STRICT_HEADER_ALLOWLIST,
    },
    "pci": {
        "redaction_mode": "mask",
        "redacted_fields": DEFAULT_REDACTED_FIELDS + PCI_REDACTED_FIELDS,
        "log_request_body": False,
        "log_response_body": False,
    },
    "gdpr": {
        "redaction_mode": "hash",
        "redacted_fields": DEFAULT_REDACTED_FIELDS + GDPR_REDACTED_FIELDS,
        "log_request_body": False,
        "log_response_body": False,
    },
}
_SECURITY_POLICY_ENV_MAP = {
    "redaction_preset": "OBS_REDACTION_PRESET",
    "redacted_fields": "OBS_REDACTED_FIELDS",
    "redacted_headers": "OBS_REDACTED_HEADERS",
    "redaction_mode": "OBS_REDACTION_MODE",
    "mask_text": "OBS_MASK_TEXT",
    "log_request_body": "OBS_LOG_REQUEST_BODY",
    "log_response_body": "OBS_LOG_RESPONSE_BODY",
    "max_body_length": "OBS_MAX_BODY_LENGTH",
    "header_allowlist": "OBS_HEADER_ALLOWLIST",
    "event_key_allowlist": "OBS_EVENT_KEY_ALLOWLIST",
    "body_capture_media_types": "OBS_BODY_CAPTURE_MEDIA_TYPES",
}
_OPTIONAL_SECURITY_FIELDS = {
    "header_allowlist",
    "event_key_allowlist",
    "body_capture_media_types",
}
_DROP = object()

RedactionMode = Literal["mask", "hash", "drop"]


class SecurityPolicy(EnvLoadable, BaseModel):
    model_config = ConfigDict(frozen=True)

    redacted_fields: tuple[str, ...] = DEFAULT_REDACTED_FIELDS
    redacted_headers: tuple[str, ...] = DEFAULT_REDACTED_HEADERS
    redaction_mode: RedactionMode = "mask"
    mask_text: str = "***"
    log_request_body: bool = False
    log_response_body: bool = False
    max_body_length: int = Field(default=256, gt=0)
    header_allowlist: tuple[str, ...] | None = None
    event_key_allowlist: tuple[str, ...] | None = None
    body_capture_media_types: tuple[str, ...] | None = None

    @field_validator(
        "redacted_fields",
        "redacted_headers",
        "header_allowlist",
        "event_key_allowlist",
        mode="before",
    )
    @classmethod
    def _parse_key_tuples(
        cls, value: object, info: ValidationInfo
    ) -> tuple[str, ...] | None:
        defaults: dict[str, tuple[str, ...]] = {
            "redacted_fields": DEFAULT_REDACTED_FIELDS,
            "redacted_headers": DEFAULT_REDACTED_HEADERS,
        }
        field_name = info.field_name
        if field_name and field_name in defaults:
            return parse_csv(value, default=defaults[field_name], optional=False)
        return parse_csv(value, optional=True)

    @field_validator("body_capture_media_types", mode="before")
    @classmethod
    def _parse_media_types(cls, value: object) -> tuple[str, ...] | None:
        return parse_csv(value, optional=True)

    @field_validator(
        "redacted_fields",
        "redacted_headers",
        "header_allowlist",
        "event_key_allowlist",
        mode="after",
    )
    @classmethod
    def _normalize_key_tuples(
        cls, value: tuple[str, ...] | None
    ) -> tuple[str, ...] | None:
        if value is None:
            return None
        return tuple(_normalize_key(item) for item in value)

    @field_validator("body_capture_media_types", mode="after")
    @classmethod
    def _normalize_media_type_tuples(
        cls, value: tuple[str, ...] | None
    ) -> tuple[str, ...] | None:
        if value is None:
            return None
        return tuple(_normalize_media_type(item) for item in value)

    @classmethod
    def from_preset(cls, name: str) -> "SecurityPolicy":
        normalized_name = name.strip().lower()
        if normalized_name not in SECURITY_POLICY_PRESETS:
            available = ", ".join(sorted(SECURITY_POLICY_PRESETS))
            raise ValueError(f"Unknown security preset '{name}'. Expected one of: {available}")
        return cls(**SECURITY_POLICY_PRESETS[normalized_name])

    @classmethod
    def from_env(cls) -> "SecurityPolicy":
        env_settings = cls._env_settings_class()()
        overrides: dict[str, Any] = {}
        for field_name, env_name in _SECURITY_POLICY_ENV_MAP.items():
            if os.getenv(env_name) is None:
                continue
            value = getattr(env_settings, field_name)
            if value is None and field_name not in _OPTIONAL_SECURITY_FIELDS:
                continue
            overrides[field_name] = value
        preset_name = overrides.pop("redaction_preset", None)

        base = cls()
        if preset_name:
            base = cls.from_preset(preset_name)

        if not overrides:
            return base
        return base.model_copy(update=overrides)

    @classmethod
    def _env_settings_class(cls) -> type[BaseSettings]:
        return _SecurityPolicySettings


class TrustedProxyPolicy(EnvLoadable, BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    trusted_cidrs: tuple[str, ...] = DEFAULT_TRUSTED_CIDRS
    honor_forwarded_headers: bool = False

    @classmethod
    def _env_settings_class(cls) -> type[BaseSettings]:
        return _TrustedProxyPolicySettings


class _SecurityPolicySettings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    redaction_preset: str | None = Field(
        default=None,
        validation_alias="OBS_REDACTION_PRESET",
    )
    redacted_fields: str | tuple[str, ...] | None = Field(
        default=None,
        validation_alias="OBS_REDACTED_FIELDS",
    )
    redacted_headers: str | tuple[str, ...] | None = Field(
        default=None,
        validation_alias="OBS_REDACTED_HEADERS",
    )
    redaction_mode: RedactionMode | None = Field(
        default=None,
        validation_alias="OBS_REDACTION_MODE",
    )
    mask_text: str | None = Field(default=None, validation_alias="OBS_MASK_TEXT")
    log_request_body: bool | None = Field(
        default=None,
        validation_alias="OBS_LOG_REQUEST_BODY",
    )
    log_response_body: bool | None = Field(
        default=None,
        validation_alias="OBS_LOG_RESPONSE_BODY",
    )
    max_body_length: int | None = Field(
        default=None,
        gt=0,
        validation_alias="OBS_MAX_BODY_LENGTH",
    )
    header_allowlist: str | tuple[str, ...] | None = Field(
        default=None,
        validation_alias="OBS_HEADER_ALLOWLIST",
    )
    event_key_allowlist: str | tuple[str, ...] | None = Field(
        default=None,
        validation_alias="OBS_EVENT_KEY_ALLOWLIST",
    )
    body_capture_media_types: str | tuple[str, ...] | None = Field(
        default=None,
        validation_alias="OBS_BODY_CAPTURE_MEDIA_TYPES",
    )

    @field_validator(
        "redacted_fields",
        "redacted_headers",
        "header_allowlist",
        "event_key_allowlist",
        "body_capture_media_types",
        mode="before",
    )
    @classmethod
    def _parse_tuple_values(
        cls, value: object, info: ValidationInfo
    ) -> tuple[str, ...] | None:
        # Settings parsing must normalize raw env strings before SecurityPolicy model validation.
        field_name = info.field_name
        if field_name == "redacted_fields":
            return parse_csv(value, default=DEFAULT_REDACTED_FIELDS, optional=False)
        if field_name == "redacted_headers":
            return parse_csv(value, default=DEFAULT_REDACTED_HEADERS, optional=False)
        return parse_csv(value, optional=True)


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
        parsed = parse_csv(value, default=DEFAULT_TRUSTED_CIDRS, optional=False)
        if isinstance(parsed, tuple):
            return parsed
        return DEFAULT_TRUSTED_CIDRS

__all__ = [
    "DEFAULT_REDACTED_FIELDS",
    "DEFAULT_REDACTED_HEADERS",
    "STRICT_HEADER_ALLOWLIST",
    "PCI_REDACTED_FIELDS",
    "GDPR_REDACTED_FIELDS",
    "SECURITY_POLICY_PRESETS",
    "DEFAULT_TRUSTED_CIDRS",
    "RedactionMode",
    "SecurityPolicy",
    "TrustedProxyPolicy",
    "_DROP",
]
