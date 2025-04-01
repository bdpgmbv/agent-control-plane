"""
LAYER 0 - SHARED: STRUCTURED LOGGING
====================================
One JSON object per line, carrying the run id and - for worker threads - which
sub-question the line belongs to.

That second field matters here more than in a single-threaded system. Four
workers log at once, so without a sub-question id the log is four interleaved
stories with no way to separate them.
"""

import json
import logging
import sys
import time
import uuid
from contextvars import ContextVar

current_run_id: ContextVar[str] = ContextVar("current_run_id", default="-")
current_sub_question: ContextVar[str] = ContextVar("current_sub_question", default="-")


def new_run_id() -> str:
    return "run_" + uuid.uuid4().hex[:10]


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "run_id": current_run_id.get(),
            "sub_question": current_sub_question.get(),
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
