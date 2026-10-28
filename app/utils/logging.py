# What: sets up logging once (console + `data/runtime/app.log`) and hands out named loggers.
# Why: errors are hidden from the user, so the log file is where we (and the reviewer) can see
#      what really happened, including the job or thread id.
# Used by: app/main.py, app/db/repo.py, app/agent/llm.py, and every later module that logs.

import logging
from typing import Any

from app.config import get_settings

_LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_configured = False


def configure_logging(level: int = logging.INFO) -> None:
    """Attach a console handler and a file handler to the `app` logger.

    Safe to call many times (Streamlit reruns the script on every click); it only runs once.
    """
    global _configured
    if _configured:
        return

    logger = logging.getLogger("app")
    logger.setLevel(level)
    formatter = logging.Formatter(_LOG_FORMAT)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(console)

    file_handler = logging.FileHandler(get_settings().runtime_dir / "app.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Return a logger under the `app` namespace, e.g. `get_logger(__name__)`."""
    configure_logging()
    return logging.getLogger(name if name.startswith("app") else f"app.{name}")


def log_exception(logger: logging.Logger, message: str, **context: Any) -> None:
    """Log the current exception with its stack trace and context such as `job_id=3`.

    Call this inside an `except` block.
    """
    ctx = " ".join(f"{key}={value}" for key, value in context.items())
    logger.exception("%s %s", message, ctx)
