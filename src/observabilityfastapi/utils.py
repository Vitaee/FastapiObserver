from __future__ import annotations

from typing import Any


def normalize_path(path: str, *, default: str = "/") -> str:
    candidate = path.strip() if isinstance(path, str) else ""
    candidate = candidate or default
    if not candidate.startswith("/"):
        candidate = f"/{candidate}"
    if len(candidate) > 1:
        candidate = candidate.rstrip("/")
    return candidate


def parse_csv_tuple(value: Any, default: tuple[str, ...]) -> tuple[str, ...]:
    if value is None:
        return default
    if isinstance(value, str):
        items = tuple(part.strip() for part in value.split(",") if part.strip())
        return items or default
    if isinstance(value, tuple):
        items = tuple(str(item).strip() for item in value if str(item).strip())
        return items or default
    if isinstance(value, list):
        items = tuple(str(item).strip() for item in value if str(item).strip())
        return items or default
    return default
