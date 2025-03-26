"""
LAYER 0 - SHARED: STRUCTURED LOGGING
====================================
One JSON object per line, carrying both the request id AND the conversation id.

The conversation id is the addition that matters here. A support complaint is
never about one request - it is "the agent told me the wrong thing this
afternoon". You need every line from that whole conversation, in order.

Log lines go through PII redaction on the way out. A log file is a place personal
data goes to live forever, in a system with far weaker access control than your
database.
"""

import json
import logging
import sys
import time
import uuid
from contextvars import ContextVar

current_request_id: ContextVar[str] = ContextVar("current_request_id", default="-")
current_conversation_id: ContextVar[str] = ContextVar("current_conversation_id", default="-")


def new_request_id() -> str:
    return "req_" + uuid.uuid4().hex[:10]


def new_conversation_id() -> str:
    return "conv_" + uuid.uuid4().hex[:12]


class JsonLogFormatter(logging.Formatter):
    """Turn a log record into one JSON line, with personal data removed."""

    def format(self, record: logging.LogRecord) -> str:
        from support_agent.layer0_shared.pii import redact
        from support_agent.layer1_config.settings import settings

        message = record.getMessage()
        if settings.redact_pii_in_logs:
            message, _kinds = redact(message)

        payload = {
            "time": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "request_id": current_request_id.get(),
            "conversation_id": current_conversation_id.get(),
            "message": message,
        }

        extra_fields = getattr(record, "fields", None)
        if isinstance(extra_fields, dict):
            for key in extra_fields:
                value = extra_fields[key]
                if isinstance(value, str) and settings.redact_pii_in_logs:
                    value, _kinds = redact(value)
                payload[key] = value

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
    """Log one structured event: log_event(log, 'tool.called', tool='get_order')."""
    logger.info(message, extra={"fields": fields})
