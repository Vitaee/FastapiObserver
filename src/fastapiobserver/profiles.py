"""Environment profile logic for zero-glue setup."""

from __future__ import annotations

import os
from collections.abc import Generator
from contextlib import contextmanager

_PROFILE_DEFAULTS: dict[str, dict[str, str]] = {
    "development": {"OBS_LOG_LEVEL": "DEBUG"},
    "production": {
        "OBS_LOG_LEVEL": "INFO",
        "OBS_LOG_QUEUE_MAX_SIZE": "20000",
        "OBS_REDACTION_PRESET": "strict",
        # Costs nothing per request; see decision record 0014. Held DEBUG logs
        # (OBS_LOG_LEVEL_ON_ERROR) stay opt-in: each held record costs CPU.
        "OBS_LOOP_BLOCK_THRESHOLD_MS": "250",
    },
}


@contextmanager
def apply_profile_context() -> Generator[None, None, None]:
    """Add the defaults of the ``OBS_PROFILE`` profile to the environment while
    settings are read. Variables already set win.

    Only the variables added here are removed afterwards, so changes other
    threads make to the environment meanwhile are kept.
    """
    profile = os.getenv("OBS_PROFILE", "").strip().lower()
    added = {
        name: value
        for name, value in _PROFILE_DEFAULTS.get(profile, {}).items()
        if name not in os.environ
    }
    os.environ.update(added)
    try:
        yield
    finally:
        for name in added:
            os.environ.pop(name, None)


__all__ = ["apply_profile_context"]
