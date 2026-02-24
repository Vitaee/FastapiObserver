"""
Structured JSON logging configuration for Grafana + Loki.
Provides a StructuredJsonFormatter that outputs logs in a format optimized
for Loki ingestion with automatic request ID correlation.
"""
import logging
import os
from datetime import datetime, timezone
from logging.config import dictConfig
import orjson
from request_context import get_request_id, get_user_context

class StructuredJsonFormatter(logging.Formatter):
    """
    JSON formatter optimized for Grafana + Loki.
    Outputs logs as single-line JSON with comprehensive metadata.
    """
    def __init__(self, app_name="demo-api", environment="production"):
        super().__init__()
        self.app_name = app_name
        self.environment = environment
        self.pid = os.getpid()
    
    def format(self, record: logging.LogRecord) -> str:
        log_dict = {
            "timestamp": self._format_timestamp(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": get_request_id(),
            "trace_id": getattr(record, "trace_id", None),
            "span_id": getattr(record, "span_id", None),
            "app": self.app_name,
            "environment": self.environment,
            "pid": self.pid,
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        
        user_context = get_user_context()
        if user_context:
            log_dict["user"] = user_context
            
        extra = self._extract_extra(record)
        if "http" in extra:
            log_dict["http"] = extra.pop("http")
        if extra:
            log_dict["extra"] = extra
            
        if record.exc_info:
            log_dict["exception"] = self._format_exception(record)
            
        return orjson.dumps(log_dict, default=str).decode("utf-8")
    
    def _format_timestamp(self, record: logging.LogRecord) -> str:
        dt = datetime.fromtimestamp(record.created, tz=timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        
    def _extract_extra(self, record: logging.LogRecord) -> dict:
        standard_attrs = {
            "name", "msg", "args", "created", "filename", "funcName",
            "levelname", "levelno", "lineno", "module", "msecs",
            "pathname", "process", "processName", "relativeCreated",
            "stack_info", "exc_info", "exc_text", "thread", "threadName",
            "taskName", "message",
        }
        return {k: v for k, v in record.__dict__.items() 
        if k not in standard_attrs and not k.startswith("_")}
        
    def _format_exception(self, record: logging.LogRecord) -> dict:
        exc_type, exc_value, exc_tb = record.exc_info
        return {
            "type": exc_type.__name__ if exc_type else None,
            "message": str(exc_value) if exc_value else None,
            "traceback": self.formatException(record.exc_info) if record.exc_info else None,
        }

class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id() or "-"
        return True

def setup_logging():
    dictConfig({
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {"request_id": {"()": RequestIdFilter}},
        "formatters": {"json": {"()": lambda: StructuredJsonFormatter()}},
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "json",
                "level": "INFO",
                "filters": ["request_id"],
            }
        },
        "loggers": {
            "uvicorn.access": {"handlers": ["console"], "propagate": False},
            "demo": {"handlers": ["console"], "level": "DEBUG", "propagate": False},
        },
        "root": {"handlers": ["console"], "level": "INFO", "filters": ["request_id"]}
    })
