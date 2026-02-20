from __future__ import annotations

def _upsert_header(
    headers: list[tuple[bytes, bytes]],
    key: str,
    value: str,
) -> list[tuple[bytes, bytes]]:
    key_bytes = key.lower().encode("latin1")
    value_bytes = value.encode("latin1", "replace")
    next_headers = [(k, v) for (k, v) in headers if k.lower() != key_bytes]
    next_headers.append((key_bytes, value_bytes))
    return next_headers


def _get_header(headers: list[tuple[bytes, bytes]], target: bytes) -> str | None:
    for k, v in headers:
        if k.lower() == target:
            return v.decode("latin1")
    return None

__all__ = ["_upsert_header", "_get_header"]
