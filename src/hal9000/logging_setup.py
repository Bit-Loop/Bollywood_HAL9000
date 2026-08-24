"""Rotating file and stderr logging without credential payloads."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from hal9000.paths import AppPaths


def configure_logging(paths: AppPaths, verbose: bool = False) -> logging.Logger:
    paths.ensure()
    logger = logging.getLogger("hal9000")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.handlers.clear()
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    file_handler = RotatingFileHandler(
        paths.log_file, maxBytes=2_000_000, backupCount=4, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    if verbose:
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        logger.addHandler(stream)
    return logger
