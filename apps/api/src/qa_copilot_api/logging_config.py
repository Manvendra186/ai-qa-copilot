"""Structured JSON logging (build bible §31.5: "structured JSON logs").

Stdlib-only: one JSON formatter over the standard ``logging`` hierarchy, so
uvicorn/Starlette and every future module (AI gateway, execution) emit the
same machine-readable lines. ``extra={"key": value}`` on any ``logger`` call
lands as top-level fields in the JSON payload.
"""

import json
import logging
from datetime import UTC, datetime
from typing import Any

#: LogRecord attributes that are part of the stdlib record, not user extras.
_RESERVED_FIELDS = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
        "message",
        "asctime",
        # uvicorn's ANSI template — the rendered text is already in `message`.
        "color_message",
    }
)

_HANDLER_MARK = "_qa_copilot_json_handler"


class JsonFormatter(logging.Formatter):
    """Render a ``LogRecord`` as a single JSON line.

    Fixed fields: ``timestamp`` (UTC ISO-8601), ``level``, ``logger``,
    ``message``. Any non-reserved ``extra`` keys are promoted to top-level
    fields; ``logger.exception(...)`` adds an ``exception`` field with the
    rendered traceback. Non-serializable values fall back to ``str()``.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED_FIELDS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        elif record.stack_info:
            payload["stack_info"] = self.formatStack(record.stack_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Point the root logger (and uvicorn's loggers) at a JSON handler.

    Idempotent: calling it again (e.g. per ``create_app`` in tests) replaces
    our handler instead of stacking duplicates.
    """
    numeric_level = logging.getLevelName(level.upper())
    if not isinstance(numeric_level, int):
        numeric_level = logging.INFO

    root = logging.getLogger()
    root.handlers = [h for h in root.handlers if not getattr(h, _HANDLER_MARK, False)]
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    setattr(handler, _HANDLER_MARK, True)
    root.addHandler(handler)
    root.setLevel(numeric_level)

    # uvicorn installs its own formatters on these; route them through the
    # same JSON handler so server logs stay machine-readable.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv_logger = logging.getLogger(name)
        uv_logger.handlers = []
        uv_logger.propagate = True
        uv_logger.setLevel(numeric_level)
