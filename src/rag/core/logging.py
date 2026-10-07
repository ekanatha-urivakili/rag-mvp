import json
import logging
import re
import sys
from contextvars import ContextVar
from datetime import UTC, datetime

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

# Defence in depth: never let tokens, keys or passwords reach log sinks (OWASP A09).
_REDACT = [
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9\-._~+/]+=*"), "Bearer [REDACTED]"),
    (re.compile(r"rk_[A-Za-z0-9]+_[A-Za-z0-9_\-]+"), "rk_[REDACTED]"),
    (re.compile(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"), "[JWT REDACTED]"),
    (re.compile(r"(?i)(password|token|secret|api_key)(['\"]?\s*[:=]\s*['\"]?)[^\s'\",}]+"), r"\1\2[REDACTED]"),
]


def redact(text: str) -> str:
    for pattern, repl in _REDACT:
        text = pattern.sub(repl, text)
    return text


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
        }
        if rid := request_id_var.get():
            payload["request_id"] = rid
        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict):
            payload.update(extra)
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info))
        return redact(json.dumps(payload, default=str))


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    for noisy in ("httpx", "httpcore", "botocore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def log_extra(**fields: object) -> dict[str, object]:
    return {"extra_fields": fields}
