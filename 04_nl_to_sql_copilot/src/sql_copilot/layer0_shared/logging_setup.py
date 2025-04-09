"""
LAYER 0 - SHARED: STRUCTURED LOGGING
====================================
One JSON object per line, carrying the request id.

Generated SQL is logged in full, every time, allowed or refused. When somebody
asks why a query was blocked - or why one that should have been blocked was not -
the exact text is the only useful evidence.
"""

import json
import logging
import sys
import time
import uuid
from contextvars import ContextVar

current_request_id: ContextVar[str] = ContextVar("current_request_id", default="-")


def new_request_id() -> str:
    return "req_" + uuid.uuid4().hex[:10]


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "request_id": current_request_id.get(),
            "message": record.getMessage(),
        }

        extra_fields = getattr(record, "fields", None)
        if isinstance(extra_fields, dict):
            for key in extra_fields:
                payload[key] = extra_fields[key]

        if record.exc_info is not None:
            payload["error"] = self.formatException(record.exc_info)

        return json.dumps(payload)


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLogFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_event(logger: logging.Logger, message: str, **fields) -> None:
    logger.info(message, extra={"fields": fields})
