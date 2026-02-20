from __future__ import annotations

import logging
from .handler import _LogtailHandler

class LogtailSink:
    """Ships structured JSON logs to Logtail (Better Stack) via HTTP.

    Example
    -------
    >>> settings = ObservabilitySettings(
    ...     logtail_enabled=True,
    ...     logtail_source_token="my-token",
    ... )
    """

    ENDPOINT = "https://in.logs.betterstack.com"

    def __init__(
        self,
        source_token: str,
        *,
        endpoint: str | None = None,
        batch_size: int = 50,
        flush_interval: float = 2.0,
        dlq_enabled: bool = False,
        dlq_dir: str = ".dlq/logtail",
        dlq_filename: str = "logtail_dlq.ndjson",
        dlq_max_bytes: int = 50 * 1024 * 1024,
        dlq_backup_count: int = 10,
        dlq_compress: bool = True,
    ) -> None:
        self._source_token = source_token
        self._endpoint = endpoint or self.ENDPOINT
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._dlq_enabled = dlq_enabled
        self._dlq_dir = dlq_dir
        self._dlq_filename = dlq_filename
        self._dlq_max_bytes = dlq_max_bytes
        self._dlq_backup_count = dlq_backup_count
        self._dlq_compress = dlq_compress

    @property
    def name(self) -> str:
        return "logtail"

    def create_handler(self, formatter: logging.Formatter) -> logging.Handler:
        handler = _LogtailHandler(
            source_token=self._source_token,
            endpoint=self._endpoint,
            batch_size=self._batch_size,
            flush_interval=self._flush_interval,
        )
        if self._dlq_enabled:
            handler.enable_dlq(
                directory=self._dlq_dir,
                filename=self._dlq_filename,
                max_bytes=self._dlq_max_bytes,
                backup_count=self._dlq_backup_count,
                compress=self._dlq_compress,
            )
        handler.setFormatter(formatter)
        return handler

__all__ = ["LogtailSink"]
