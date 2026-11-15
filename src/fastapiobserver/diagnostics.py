"""Where each diagnostic is reported as it is noticed, so metrics can count it.

Reported before any log rate limit: a warning is written at most once a minute
or once per statement, and its counter sees every occurrence.
"""

from __future__ import annotations

from collections.abc import Callable

# Called with the diagnostic's name, a label value (or tuple of label values)
# and how many times it happened, on the thread that noticed it.
_listeners: list[Callable[[str, str | tuple[str, ...], int], None]] = []


def report_diagnostic(name: str, label: str | tuple[str, ...] = "", count: int = 1) -> None:
    for listener in _listeners:
        try:
            listener(name, label, count)
        except Exception:
            # Counting a diagnostic must never fail the request it describes.
            pass


__all__ = ["report_diagnostic"]
