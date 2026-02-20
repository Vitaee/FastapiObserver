def _normalize_key(key: str) -> str:
    return key.strip().lower().replace("_", "-")


def _normalize_media_type(value: str) -> str:
    return value.split(";", 1)[0].strip().lower()

__all__ = ["_normalize_key", "_normalize_media_type"]
