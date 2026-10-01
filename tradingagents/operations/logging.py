"""Idempotent size-bounded logging for long-running local deployments."""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

_HANDLER_NAME = "tradingagents-rotating-file"


def _bounded_int(env_name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(env_name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{env_name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{env_name} must be between {minimum} and {maximum}")
    return value


def configure_bounded_logging(log_directory: str | Path | None = None) -> Path:
    """Attach one rotating application log handler and return its path."""
    root = logging.getLogger()
    for handler in root.handlers:
        if getattr(handler, "name", None) == _HANDLER_NAME:
            return Path(handler.baseFilename)

    directory = Path(
        log_directory
        or os.environ.get("TRADINGAGENTS_LOG_DIR", Path.home() / ".tradingagents" / "logs")
    ).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    log_path = directory / "tradingagents.log"
    handler = RotatingFileHandler(
        log_path,
        maxBytes=_bounded_int("TRADINGAGENTS_LOG_MAX_BYTES", 5 * 1024 * 1024, 1024, 100 * 1024 * 1024),
        backupCount=_bounded_int("TRADINGAGENTS_LOG_BACKUP_COUNT", 3, 1, 20),
        encoding="utf-8",
    )
    handler.name = _HANDLER_NAME
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    root.addHandler(handler)
    if root.level == logging.NOTSET or root.level > logging.INFO:
        root.setLevel(logging.INFO)
    project_logger = logging.getLogger("tradingagents")
    project_logger.disabled = False
    project_logger.setLevel(logging.INFO)
    for name in ("uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.disabled = False
        if not uvicorn_logger.propagate and handler not in uvicorn_logger.handlers:
            uvicorn_logger.addHandler(handler)
    return log_path
