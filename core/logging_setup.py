"""Logging configuration for the HINT Study Console.

Applies config/logging.yaml via logging.config.dictConfig, resolving the
file handler's log file against the app's configured logs_dir. All app
code should use ``logging.getLogger(__name__)`` rather than print().
"""

from __future__ import annotations

import copy
import logging
import logging.config
from pathlib import Path

import yaml


def setup_logging(logs_dir: Path, config_path: Path) -> None:
    """Configure the root logger from config_path, writing into logs_dir.

    Falls back to a basic console-only configuration (with a warning) if
    the YAML file is missing or malformed, so a broken logging config can
    never prevent the application from starting.
    """
    logs_dir.mkdir(parents=True, exist_ok=True)

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            log_config = yaml.safe_load(f)
        log_config = copy.deepcopy(log_config)

        file_handler = log_config.get("handlers", {}).get("file")
        if file_handler is not None:
            filename = file_handler.get("filename", "system.log")
            file_handler["filename"] = str(logs_dir / filename)

        logging.config.dictConfig(log_config)
        logging.getLogger(__name__).debug("Logging configured from %s", config_path)
    except Exception as exc:  # noqa: BLE001 - logging setup must never crash the app
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        )
        logging.getLogger(__name__).warning(
            "Failed to load logging config from %s (%s). Using basic console logging.",
            config_path,
            exc,
        )
