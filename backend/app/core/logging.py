"""
Centralized logging setup.

WHY: Requirement 9 asks for "meaningful logging at each major stage"
(validation, OCR/model calls, exceptions, processing) — but also says
we must NEVER leak stack traces or secrets to the *end user* via the API
response. The solution: log generously to the SERVER-SIDE log (console /
file), but keep API error responses generic and structured (see
app/utils/errors.py, added when we build the API layer).

Usage in any module:
    from app.core.logging import get_logger
    logger = get_logger(__name__)
    logger.info("Something happened")
    logger.exception("Something failed")  # includes traceback in the log only
"""
import logging
import sys

from app.core.config import settings

_CONFIGURED = False


def configure_logging() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    root = logging.getLogger()
    root.setLevel(settings.LOG_LEVEL)

    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)

    # Avoid duplicate handlers if this gets called more than once (e.g. reload)
    root.handlers.clear()
    root.addHandler(handler)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(name)
