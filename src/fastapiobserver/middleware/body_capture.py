from __future__ import annotations

from starlette.types import Message


class _BodyCapture:
    def __init__(self, *, enabled: bool, max_length: int) -> None:
        self.enabled = enabled
        self.max_length = max_length
        self._buffer = bytearray()

    def capture_from_message(self, message: Message, message_type: str) -> None:
        if not self.enabled:
            return
        if message.get("type") != message_type:
            return
        body = message.get("body")
        if not body:
            return
        self._append(body if isinstance(body, bytes) else bytes(body))

    @property
    def value(self) -> str | None:
        if not self.enabled or not self._buffer:
            return None
        return self._buffer.decode("utf-8", "replace")

    def _append(self, chunk: bytes) -> None:
        remaining = self.max_length - len(self._buffer)
        if remaining <= 0:
            return
        self._buffer.extend(chunk[:remaining])

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled

__all__ = ["_BodyCapture"]
