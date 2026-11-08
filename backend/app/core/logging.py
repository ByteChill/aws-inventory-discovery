import json
import logging
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

# One context variable per field that should appear on every log line.
CONTEXT_FIELDS = ("task_id", "scan_request_id", "account_id", "region", "resource_type")
_context: dict[str, ContextVar[Any]] = {
    name: ContextVar(name, default=None) for name in CONTEXT_FIELDS
}

# Attributes every LogRecord has; anything else was passed through `extra=`.
_STANDARD_ATTRS = frozenset(vars(logging.makeLogRecord({}))) | {"message", "asctime"}
_SENSITIVE_KEYS = (
    "secret",
    "password",
    "token",
    "credential",
    "authorization",
    "access_key",
)


@contextmanager
def log_context(**fields: Any):
    """Attach fields (task_id, account_id, ...) to every log line inside the block."""
    tokens = [(_context[name], _context[name].set(value)) for name, value in fields.items()]
    try:
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for name, var in _context.items():
            value = var.get()
            if value is not None:
                payload[name] = value
        for key, value in vars(record).items():
            if key in _STANDARD_ATTRS or key in payload:
                continue
            payload[key] = "[redacted]" if _is_sensitive(key) else value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def _is_sensitive(key: str) -> bool:
    lowered = key.lower()
    return any(word in lowered for word in _SENSITIVE_KEYS)


def setup_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
