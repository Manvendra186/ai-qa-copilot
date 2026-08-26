"""S0.3 tests: structured JSON logging (build bible §31.5)."""

import io
import json
import logging
from typing import Any

from qa_copilot_api.logging_config import _HANDLER_MARK, JsonFormatter, configure_logging


def _capture() -> tuple[logging.Logger, io.StringIO]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("qa_copilot_api.test")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    return logger, stream


def _last_entry(stream: io.StringIO) -> dict[str, Any]:
    lines = [line for line in stream.getvalue().splitlines() if line.strip()]
    parsed: Any = json.loads(lines[-1])
    assert isinstance(parsed, dict)
    return parsed


def test_json_line_has_core_fields() -> None:
    logger, stream = _capture()
    logger.info("hello")
    entry = _last_entry(stream)
    assert entry["message"] == "hello"
    assert entry["level"] == "INFO"
    assert entry["logger"] == "qa_copilot_api.test"
    assert entry["timestamp"].endswith("+00:00")  # UTC ISO-8601


def test_extra_fields_promoted_to_top_level() -> None:
    logger, stream = _capture()
    logger.info("job started", extra={"job_id": "job-1", "model": "local-7b"})
    entry = _last_entry(stream)
    assert entry["job_id"] == "job-1"
    assert entry["model"] == "local-7b"
    # stdlib record fields must not leak
    assert "args" not in entry
    assert "pathname" not in entry


def test_exception_rendered_into_field() -> None:
    logger, stream = _capture()
    try:
        raise ValueError("boom")
    except ValueError:
        logger.exception("failure analyzed")
    entry = _last_entry(stream)
    assert entry["level"] == "ERROR"
    assert "ValueError: boom" in entry["exception"]


def test_non_serializable_extra_falls_back_to_str() -> None:
    logger, stream = _capture()

    class Unserializable:
        def __str__(self) -> str:
            return "unserializable-object"

    logger.warning("state", extra={"state": Unserializable()})
    entry = _last_entry(stream)
    assert entry["state"] == "unserializable-object"


def test_configure_logging_is_idempotent() -> None:
    configure_logging("INFO")
    configure_logging("DEBUG")
    ours = [h for h in logging.getLogger().handlers if getattr(h, _HANDLER_MARK, False)]
    assert len(ours) == 1
    assert logging.getLogger().level == logging.DEBUG


def test_configure_logging_routes_uvicorn_loggers() -> None:
    configure_logging("INFO")
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv_logger = logging.getLogger(name)
        assert uv_logger.handlers == []
        assert uv_logger.propagate is True
