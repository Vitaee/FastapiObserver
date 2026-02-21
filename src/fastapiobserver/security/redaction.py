import hashlib
from typing import Any, Mapping

from .normalize import _normalize_key, _normalize_media_type
from .policy import _DROP, SecurityPolicy

MAX_SANITIZE_DEPTH = 32


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
    header_allowlist = (
        {_normalize_key(item) for item in policy.header_allowlist}
        if policy.header_allowlist is not None
        else None
    )
    event_key_allowlist = (
        {_normalize_key(item) for item in policy.event_key_allowlist}
        if policy.event_key_allowlist is not None
        else None
    )
    return _sanitize_mapping(
        data,
        policy,
        sensitive_fields,
        sensitive_headers,
        parent_is_headers=False,
        depth=0,
        header_allowlist=header_allowlist,
        event_key_allowlist=event_key_allowlist,
    )


def _sanitize_mapping(
    data: Mapping[str, Any],
    policy: SecurityPolicy,
    sensitive_fields: set[str],
    sensitive_headers: set[str],
    parent_is_headers: bool,
    depth: int,
    header_allowlist: set[str] | None,
    event_key_allowlist: set[str] | None,
) -> dict[str, Any]:
    if depth > MAX_SANITIZE_DEPTH:
        return dict(data)
    out: dict[str, Any] = {}
    for key, value in data.items():
        normalized_key = _normalize_key(str(key))

        if parent_is_headers and header_allowlist is not None:
            if normalized_key not in header_allowlist:
                continue
        if depth == 0 and event_key_allowlist is not None:
            if normalized_key not in event_key_allowlist:
                continue

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
            depth=depth + 1,
            header_allowlist=header_allowlist,
            event_key_allowlist=event_key_allowlist,
        )
    return out


def _sanitize_value(
    value: Any,
    policy: SecurityPolicy,
    sensitive_fields: set[str],
    sensitive_headers: set[str],
    parent_is_headers: bool,
    depth: int,
    header_allowlist: set[str] | None,
    event_key_allowlist: set[str] | None,
) -> Any:
    if isinstance(value, Mapping):
        string_mapping = {str(k): v for k, v in value.items()}
        return _sanitize_mapping(
            string_mapping,
            policy,
            sensitive_fields,
            sensitive_headers,
            parent_is_headers,
            depth,
            header_allowlist,
            event_key_allowlist,
        )
    if isinstance(value, list):
        return [
            _sanitize_value(
                item,
                policy,
                sensitive_fields,
                sensitive_headers,
                parent_is_headers=False,
                depth=depth + 1,
                header_allowlist=header_allowlist,
                event_key_allowlist=event_key_allowlist,
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
                depth=depth + 1,
                header_allowlist=header_allowlist,
                event_key_allowlist=event_key_allowlist,
            )
            for item in value
        )
    return value


def is_body_capturable(content_type: str | None, policy: SecurityPolicy) -> bool:
    if policy.body_capture_media_types is None:
        return True
    if not content_type:
        return False
    normalized_content_type = _normalize_media_type(content_type)
    for media_type in policy.body_capture_media_types:
        if normalized_content_type.startswith(media_type):
            return True
    return False

__all__ = ["sanitize_event", "is_body_capturable"]
