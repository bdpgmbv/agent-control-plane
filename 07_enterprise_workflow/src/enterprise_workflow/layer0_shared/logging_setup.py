"""
LAYER 0 - SHARED: STRUCTURED LOGGING
====================================
Production systems are read by machines, not only by people. So every log line
is one JSON object. You can then search them ("show me every request slower
than 2 seconds") instead of reading text.
"""

import json
import logging
import sys
import time
import uuid
from contextvars import ContextVar

# The id of the request currently being handled. Set by the API layer.
# A ContextVar keeps one value per request even when many run at the same time.
current_request_id: ContextVar[str] = ContextVar("current_request_id", default="-")


def new_request_id() -> str:
    """Create a short unique id for one incoming request."""
    return uuid.uuid4().hex[:12]


class JsonLogFormatter(logging.Formatter):
    """Turn a log record into a single JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "request_id": current_request_id.get(),
            "message": record.getMessage(),
        }

        # Anything passed as logger.info("msg", extra={"fields": {...}}).
        extra_fields = getattr(record, "fields", None)
        if isinstance(extra_fields, dict):
            for key in extra_fields:
                payload[key] = extra_fields[key]

        if record.exc_info is not None:
            payload["error"] = self.formatException(record.exc_info)

        return json.dumps(payload)


def setup_logging(level: str = "INFO") -> None:
    """Install the JSON formatter once, at application start."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLogFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    # uvicorn is chatty about access logs; keep them but at the same format.
    logging.getLogger("uvicorn.access").propagate = True


def get_logger(name: str) -> logging.Logger:
    """Get a logger for one module."""
    return logging.getLogger(name)


def log_event(logger: logging.Logger, message: str, **fields) -> None:
    """
    Log one structured event.

    Example:
        log_event(log, "retrieval.finished", hits=12, latency_ms=340)
    """
    logger.info(message, extra={"fields": fields})
