import functools
import hashlib
import hmac
import json
import re
import secrets
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qsl, urlencode

from .normalize import _normalize_key, _normalize_media_type
from .policy import _DROP, SecurityPolicy

MAX_SANITIZE_DEPTH = 32
_SCALAR_TYPES = frozenset({str, int, float, bool})
_PROCESS_KEY = secrets.token_bytes(32)  # for policies without a hash_key
# The user and password in a URL such as postgresql://user:password@host, which
# connection errors repeat in their message. Neither part can hold whitespace,
# "/", "?", "#" or "@" unencoded, so a match never runs past the URL.
_URL_CREDENTIALS = re.compile(r"://[^\s/?#@:]*:[^\s/?#@]+@")


@functools.lru_cache(maxsize=64)
def _policy_key_sets(
    policy: SecurityPolicy,
) -> tuple[frozenset[str], frozenset[str], frozenset[str] | None, frozenset[str] | None]:
    # Policies are frozen, so the normalized lookup sets are built once per policy
    # instead of once per log record.
    def normalized(items: tuple[str, ...]) -> frozenset[str]:
        return frozenset(_normalize_key(item) for item in items)

    return (
        normalized(policy.redacted_fields),
        normalized(policy.redacted_headers),
        normalized(policy.header_allowlist) if policy.header_allowlist is not None else None,
        normalized(policy.event_key_allowlist)
        if policy.event_key_allowlist is not None
        else None,
    )


def _redact_value(value: Any, policy: SecurityPolicy) -> Any:
    if policy.redaction_mode == "drop":
        return _DROP
    if policy.redaction_mode == "hash":
        # Keyed: a plain hash of an email or phone number is reversed by
        # hashing candidates until one matches.
        key = policy.hash_key.get_secret_value().encode() if policy.hash_key else _PROCESS_KEY
        # JSON permits escaped unpaired surrogates; hashing them must not fail
        # request finalization. Valid Unicode keeps its existing byte encoding.
        encoded = str(value).encode("utf-8", errors="surrogatepass")
        digest = hmac.new(key, encoded, hashlib.sha256).hexdigest()
        return f"hmac-sha256:{digest}"
    return policy.mask_text


def redact_body(body: str, policy: SecurityPolicy) -> str:
    """Decode JSON/form keys before redaction; mask bodies that cannot be inspected.

    Capture can cut JSON mid-value, so malformed JSON must never fall back to
    another parser. Body ``drop`` mode keeps its existing masking behavior.
    """
    if policy.redaction_mode == "drop":
        policy = policy.model_copy(update={"redaction_mode": "mask"})
    sensitive_fields, sensitive_headers, header_allowlist, _ = _policy_key_sets(policy)
    try:
        value = json.loads(body)
    except (ValueError, RecursionError):
        if body.lstrip().startswith(("{", "[", '"', "\ufeff")):
            return policy.mask_text
        try:
            pairs = parse_qsl(body, keep_blank_values=True, strict_parsing=True, errors="strict")
        except ValueError:
            return policy.mask_text
        return urlencode(
            [
                (
                    key,
                    _redact_value(value, policy)
                    if _normalize_key(key) in sensitive_fields
                    else value,
                )
                for key, value in pairs
            ],
            safe="*",
        )
    sanitized = _sanitize_value(
        value,
        policy,
        sensitive_fields,
        sensitive_headers,
        parent_is_headers=False,
        depth=0,
        header_allowlist=header_allowlist,
        event_key_allowlist=None,
    )
    return json.dumps(sanitized, separators=(",", ":"))


def strip_url_credentials(text: str) -> str:
    """*text* with the user and password of every URL in it masked."""
    return _URL_CREDENTIALS.sub("://***:***@", text)


def sanitize_event(data: dict[str, Any], policy: SecurityPolicy) -> dict[str, Any]:
    """*data* redacted, keeping only the event-key allowlist's keys, if there is one."""
    return _sanitize(data, policy, apply_event_key_allowlist=True)


def sanitize_record(data: dict[str, Any], policy: SecurityPolicy) -> dict[str, Any]:
    """A log record's fields redacted as by :func:`sanitize_event`, with the
    event-key allowlist applied to their ``event`` only: the record's own fields,
    such as ``timestamp`` and ``request_id``, aren't event keys."""
    sanitized = _sanitize(data, policy, apply_event_key_allowlist=False)
    event = data.get("event")
    if policy.event_key_allowlist is not None and isinstance(event, dict) and "event" in sanitized:
        sanitized["event"] = sanitize_event(event, policy)
    return sanitized


def _sanitize(
    data: Mapping[str, Any], policy: SecurityPolicy, *, apply_event_key_allowlist: bool
) -> dict[str, Any]:
    sensitive_fields, sensitive_headers, header_allowlist, event_key_allowlist = (
        _policy_key_sets(policy)
    )
    return _sanitize_mapping(
        data,
        policy,
        sensitive_fields,
        sensitive_headers,
        parent_is_headers=False,
        depth=0,
        header_allowlist=header_allowlist,
        event_key_allowlist=event_key_allowlist if apply_event_key_allowlist else None,
    )


def _sanitize_mapping(
    data: Mapping[str, Any],
    policy: SecurityPolicy,
    sensitive_fields: frozenset[str],
    sensitive_headers: frozenset[str],
    parent_is_headers: bool,
    depth: int,
    header_allowlist: frozenset[str] | None,
    event_key_allowlist: frozenset[str] | None,
) -> dict[str, Any]:
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

        if value is None or type(value) in _SCALAR_TYPES:
            out[str(key)] = value
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
    sensitive_fields: frozenset[str],
    sensitive_headers: frozenset[str],
    parent_is_headers: bool,
    depth: int,
    header_allowlist: frozenset[str] | None,
    event_key_allowlist: frozenset[str] | None,
) -> Any:
    if depth > MAX_SANITIZE_DEPTH and isinstance(value, (Mapping, list, tuple)):
        # Too deep to inspect: mask rather than pass unchecked data through.
        return policy.mask_text
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

__all__ = [
    "sanitize_event",
    "sanitize_record",
    "redact_body",
    "strip_url_credentials",
    "is_body_capturable",
]
