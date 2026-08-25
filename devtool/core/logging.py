from __future__ import annotations

"""Central logging configuration for Devtool's architecture surfaces."""

from dataclasses import dataclass
import logging
import sys


@dataclass(frozen=True, slots=True)
class LoggingMode:
    verbose: bool = False
    quiet: bool = False
    trace: bool = False


_MODE = LoggingMode()
_ROOT_NAME = "devtool"


def configure_logging(*, verbose: bool = False, quiet: bool = False, trace: bool = False) -> LoggingMode:
    global _MODE
    _MODE = LoggingMode(verbose=bool(verbose), quiet=bool(quiet), trace=bool(trace))
    logger = logging.getLogger(_ROOT_NAME)
    logger.handlers.clear()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("[%(name)s] %(levelname)s: %(message)s"))
    logger.addHandler(handler)
    if trace:
        logger.setLevel(logging.DEBUG)
    elif quiet:
        logger.setLevel(logging.ERROR)
    elif verbose:
        logger.setLevel(logging.INFO)
    else:
        logger.setLevel(logging.WARNING)
    logger.propagate = False
    return _MODE


def logging_mode() -> LoggingMode:
    return _MODE


def get_logger(category: str) -> logging.Logger:
    normalized = str(category or "CORE").strip().upper().replace(" ", "-")
    return logging.getLogger(f"{_ROOT_NAME}.{normalized}")


__all__ = ["LoggingMode", "configure_logging", "logging_mode", "get_logger"]
