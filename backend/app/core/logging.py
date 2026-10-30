"""Structured JSON logging with secret redaction."""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any

from app.core.request_context import get_request_id

_SENSITIVE = re.compile(
    r"(pass(word)?|secret|token|authorization|api[_-]?key|otp|code_hash|credential)",
    re.IGNORECASE,
)
_RESERVED = set(vars(logging.makeLogRecord({})).keys()) | {"message", "asctime"}
# Credentials that can appear inside free text (messages, exception text, header values):
# "Bearer <token>" and JWT-shaped strings.
_INLINE = re.compile(
    r"(?i)\b(bearer)\s+[A-Za-z0-9\-._~+/]+=*"
    r"|\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}"
)


def scrub(text: str) -> str:
    """Mask inline credentials in free text."""
    return _INLINE.sub(lambda m: f"{m.group(1)} [REDACTED]" if m.group(1) else "[REDACTED]",
                       text)


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: ("[REDACTED]" if _SENSITIVE.search(str(k)) else redact(v))
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return scrub(value)
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": scrub(record.getMessage()),
            "request_id": get_request_id(),
        }
        extras = {k: v for k, v in record.__dict__.items() if k not in _RESERVED}
        if extras:
            payload.update(redact(extras))
        if record.exc_info:
            payload["exc"] = scrub(self.formatException(record.exc_info))
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    for noisy in ("uvicorn.access",):
        logging.getLogger(noisy).setLevel(logging.WARNING)
