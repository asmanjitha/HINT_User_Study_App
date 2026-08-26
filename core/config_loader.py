"""YAML configuration loading for the HINT Study Console.

Loads config/app.yaml and config/study.yaml (and, once devices exist,
config/devices.yaml) into plain dicts, validates the handful of fields the
rest of the app depends on, and resolves relative paths against the
project root so the app can be launched from any working directory.

Kept deliberately simple (no schema library) -- the study protocol config
is human-edited YAML and researchers should get a clear, specific error
message rather than a stack trace from a validation framework.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import yaml

from models.enums import AppMode

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_DIR = PROJECT_ROOT / "config"


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid."""


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        raise ConfigError(f"Required configuration file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"Configuration file did not parse to a mapping: {path}")
    return data


@dataclass
class AppConfig:
    """Validated, resolved application configuration."""

    raw: dict
    """Full raw dict, in case a caller needs a field not surfaced below."""

    mode: AppMode
    data_dir: Path
    logs_dir: Path
    identifiable_db: Path
    experimental_db: Path
    backup_destination: str

    study_raw: dict
    """Raw parsed study.yaml -- the protocol config snapshotted per session."""

    config_dir: Path

    @property
    def session_max_minutes(self) -> int:
        return int(self.study_raw.get("timing", {}).get("session_max_minutes", 60))

    @property
    def continuous_task_max_minutes(self) -> int:
        return int(self.study_raw.get("timing", {}).get("continuous_task_max_minutes", 20))

    @property
    def study_version(self) -> str:
        return str(self.study_raw.get("study_version", "unversioned"))


def _resolve(base: Path, maybe_relative: str) -> Path:
    p = Path(maybe_relative)
    return p if p.is_absolute() else (base / p)


def load_config(config_dir: Optional[Path] = None) -> AppConfig:
    """Load and validate app.yaml + study.yaml.

    Raises ConfigError with a clear message if required files/fields are
    missing, rather than letting a raw KeyError/FileNotFoundError propagate.
    """
    config_dir = Path(config_dir) if config_dir else DEFAULT_CONFIG_DIR

    app_raw = _load_yaml(config_dir / "app.yaml")
    study_raw = _load_yaml(config_dir / "study.yaml")

    app_section = app_raw.get("app", {})
    paths_section = app_raw.get("paths", {})
    backup_section = app_raw.get("backup", {})

    mode_str = str(app_section.get("mode", "DEVELOPMENT")).upper()
    try:
        mode = AppMode(mode_str)
    except ValueError as exc:
        raise ConfigError(
            f"app.yaml: app.mode must be 'DEVELOPMENT' or 'STUDY', got '{mode_str}'"
        ) from exc

    try:
        data_dir_raw = paths_section["data_dir"]
        logs_dir_raw = paths_section["logs_dir"]
        identifiable_db_raw = paths_section["identifiable_db"]
        experimental_db_raw = paths_section["experimental_db"]
    except KeyError as exc:
        raise ConfigError(f"app.yaml: missing required paths.{exc.args[0]}") from exc

    return AppConfig(
        raw=copy.deepcopy(app_raw),
        mode=mode,
        data_dir=_resolve(PROJECT_ROOT, data_dir_raw),
        logs_dir=_resolve(PROJECT_ROOT, logs_dir_raw),
        identifiable_db=_resolve(PROJECT_ROOT, identifiable_db_raw),
        experimental_db=_resolve(PROJECT_ROOT, experimental_db_raw),
        backup_destination=str(backup_section.get("destination", "")),
        study_raw=copy.deepcopy(study_raw),
        config_dir=config_dir,
    )
