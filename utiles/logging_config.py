"""Central logging setup and minimal process-local error health summary."""
import logging
import os
from collections import deque
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path


class RecentErrorHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)
        self._recent: deque[tuple[float, str]] = deque(maxlen=1)

    def emit(self, record: logging.LogRecord) -> None:
        self._recent.append((record.created, record.name))

    def latest(self) -> tuple[float, str] | None:
        return self._recent[-1] if self._recent else None


_error_handler: RecentErrorHandler | None = None


def configure_logging() -> None:
    """Set console and rotating file logs once, without following a log symlink."""
    global _error_handler
    root_logger = logging.getLogger()
    if getattr(root_logger, "_converter_configured", False):
        return

    configured_level = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, configured_level, logging.INFO)
    root_logger.setLevel(level)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root_logger.addHandler(console)

    logs_dir = Path("logs")
    try:
        if logs_dir.is_symlink():
            raise OSError("logs directory must not be a symbolic link")
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_file = logs_dir / "bot.log"
        if log_file.is_symlink():
            raise OSError("log file must not be a symbolic link")
        file_handler = RotatingFileHandler(
            log_file, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
    except OSError:
        root_logger.warning("File logging is unavailable; using console output only")

    _error_handler = RecentErrorHandler()
    root_logger.addHandler(_error_handler)
    root_logger._converter_configured = True


def latest_error() -> tuple[datetime, str] | None:
    if _error_handler is None:
        return None
    recent = _error_handler.latest()
    if recent is None:
        return None
    timestamp, logger_name = recent
    return datetime.fromtimestamp(timestamp), logger_name
