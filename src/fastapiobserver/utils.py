from __future__ import annotations

import importlib
from typing import Any, Callable, TypeVar, cast

from pydantic_settings import BaseSettings, SettingsConfigDict

_NULLISH_VALUES = frozenset({"none", "null", "unset"})
TEnvLoadable = TypeVar("TEnvLoadable", bound="EnvLoadable")


def normalize_path(path: str, *, default: str = "/") -> str:
    candidate = path.strip() if isinstance(path, str) else ""
    candidate = candidate or default
    if not candidate.startswith("/"):
        candidate = f"/{candidate}"
    if len(candidate) > 1:
        candidate = candidate.rstrip("/")
    return candidate


class InternalSettingsBase(BaseSettings):
    """Base class for all environment settings models with common configuration."""

    model_config = SettingsConfigDict(
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )



class EnvLoadable:
    """Mixin for BaseModel classes that load values from a BaseSettings model."""

    @classmethod
    def _env_settings_class(cls) -> type[BaseSettings]:
        raise NotImplementedError(f"{cls.__name__} must define _env_settings_class()")

    @classmethod
    def _env_values(cls) -> dict[str, Any]:
        env_settings_cls = cls._env_settings_class()
        return env_settings_cls().model_dump()

    @classmethod
    def from_env(cls: type[TEnvLoadable]) -> TEnvLoadable:
        return cast(TEnvLoadable, cls(**cls._env_values()))


def parse_csv(
    value: object,
    *,
    default: tuple[str, ...] = (),
    optional: bool = False,
    normalize_fn: Callable[[str], str] | None = None,
    nullish_values: set[str] | frozenset[str] = _NULLISH_VALUES,
) -> tuple[str, ...] | None:
    """Parse CSV-like values into tuples.

    Behaviour:
    - ``optional=False``: returns ``default`` on empty/invalid input.
    - ``optional=True``: returns ``None`` for ``None`` or nullish markers
      (``none``, ``null``, ``unset``), and returns tuples otherwise.
    """

    items: tuple[str, ...]
    if value is None:
        return None if optional else default

    if isinstance(value, str):
        normalized_text = value.strip()
        if optional and normalized_text.lower() in nullish_values:
            return None
        items = tuple(part.strip() for part in value.split(",") if part.strip())
    elif isinstance(value, tuple):
        items = tuple(str(item).strip() for item in value if str(item).strip())
    elif isinstance(value, list):
        items = tuple(str(item).strip() for item in value if str(item).strip())
    else:
        return None if optional else default

    if normalize_fn is not None:
        normalized_items: list[str] = []
        for item in items:
            normalized_item = normalize_fn(item)
            if normalized_item:
                normalized_items.append(normalized_item)
        items = tuple(normalized_items)

    if optional:
        return items
    return items or default


def parse_csv_tuple(value: Any, default: tuple[str, ...]) -> tuple[str, ...]:
    parsed = parse_csv(value, default=default, optional=False)
    if isinstance(parsed, tuple):
        return parsed
    return default


def normalize_protocol(
    value: object,
    *,
    allowed: set[str] | frozenset[str],
    default: str | None = None,
    strict: bool = False,
    label: str = "protocol",
) -> str:
    if value is None:
        if default is not None:
            return default
        raise ValueError(f"Invalid {label}: {value}")

    normalized_value = str(value).strip().lower()
    if normalized_value in allowed:
        return normalized_value
    if strict:
        raise ValueError(f"Invalid {label}: {value}")
    if default is not None:
        return default
    raise ValueError(f"Invalid {label}: {value}")


def lazy_import(
    module_path: str,
    attr: str | None = None,
    *,
    package_hint: str | None = None,
) -> Any:
    try:
        module = importlib.import_module(module_path)
    except ModuleNotFoundError as exc:
        if package_hint:
            raise RuntimeError(
                f"Missing optional dependency for `{module_path}`. "
                f"Install `{package_hint}`."
            ) from exc
        raise

    if attr is None:
        return module

    if not hasattr(module, attr):
        raise RuntimeError(f"Module `{module_path}` does not define attribute `{attr}`")
    return getattr(module, attr)
