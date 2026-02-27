"""Environment profile logic for zero-glue setup."""

from __future__ import annotations

import os

from contextlib import contextmanager
from typing import Generator

@contextmanager
def apply_profile_context() -> Generator[None, None, None]:
    """Read OBS_PROFILE and dynamically inject profile defaults.
    
    Restores the os.environ dictionary after yielding.
    """
    profile = str(os.getenv("OBS_PROFILE", "")).strip().lower()
    
    original_env = dict(os.environ)

    try:
        if profile == "development":
            _apply_development_profile()
        elif profile == "production":
            _apply_production_profile()
            
        yield
        
    finally:
        os.environ.clear()
        os.environ.update(original_env)

def _apply_development_profile() -> None:
    """Injects development environment defaults."""
    # Verbose logging by default
    os.environ.setdefault("LOG_LEVEL", "DEBUG")
    
    # Disable OTel components by default to save resources localy
    os.environ.setdefault("OTEL_ENABLED", "false")
    os.environ.setdefault("OTEL_LOGS_ENABLED", "false")
    os.environ.setdefault("OTEL_METRICS_ENABLED", "false")
    
    # Optional: we don't enforce security redaction changes since default is mask,
    # which is already relatively loose compared to strict/hash.

def _apply_production_profile() -> None:
    """Injects production environment defaults."""
    # Standard log level
    os.environ.setdefault("LOG_LEVEL", "INFO")
    
    # Optimize log queue for high throughput
    os.environ.setdefault("LOG_QUEUE_MAX_SIZE", "20000")
    os.environ.setdefault("LOG_QUEUE_OVERFLOW_POLICY", "drop_oldest")
    
    # Automatically enforce strict redaction preset
    os.environ.setdefault("OBS_REDACTION_PRESET", "strict")

__all__ = ["apply_profile_context"]
