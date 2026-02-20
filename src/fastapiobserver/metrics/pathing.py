from __future__ import annotations

import re

_UUID_RE = re.compile(
    r"/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}"
)
_NUMBER_RE = re.compile(r"/\d+")
_HEX_RE = re.compile(r"/[0-9a-fA-F]{16,}")

def collapse_dynamic_segments(path: str) -> str:
    """Replace dynamic path segments with ``/:id`` to control label cardinality."""
    normalized = _UUID_RE.sub("/:id", path)
    normalized = _HEX_RE.sub("/:id", normalized)
    normalized = _NUMBER_RE.sub("/:id", normalized)
    return normalized

__all__ = ["collapse_dynamic_segments"]
