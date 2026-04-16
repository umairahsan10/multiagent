from __future__ import annotations

import logging

from rich.logging import RichHandler

from src.config import Config


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(Config.LOG_LEVEL)
    handler = RichHandler(rich_tracebacks=True, show_time=True, show_path=False)
    logger.addHandler(handler)
    logger.propagate = False
    return logger
