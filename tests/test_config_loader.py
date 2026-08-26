from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from core.config_loader import ConfigError, load_config
from models.enums import AppMode


def _write_config_dir(tmp_path: Path, app_overrides: dict | None = None, study_overrides: dict | None = None) -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    app_data = {
        "app": {"name": "Test App", "version": "0.0.1", "mode": "DEVELOPMENT"},
        "paths": {
            "data_dir": "data",
            "logs_dir": "logs",
            "identifiable_db": "data/identifiable.sqlite3",
            "experimental_db": "data/experimental.sqlite3",
        },
        "backup": {"destination": ""},
    }
    if app_overrides:
        app_data["app"].update(app_overrides)

    study_data = {
        "study_version": "TEST_v1",
        "timing": {"session_max_minutes": 60, "continuous_task_max_minutes": 20},
    }
    if study_overrides:
        study_data.update(study_overrides)

    with open(config_dir / "app.yaml", "w") as f:
        yaml.safe_dump(app_data, f)
    with open(config_dir / "study.yaml", "w") as f:
        yaml.safe_dump(study_data, f)

    return config_dir


def test_load_config_success(tmp_path: Path) -> None:
    config_dir = _write_config_dir(tmp_path)
    config = load_config(config_dir)

    assert config.mode == AppMode.DEVELOPMENT
    assert config.session_max_minutes == 60
    assert config.continuous_task_max_minutes == 20
    assert config.study_version == "TEST_v1"
    # Relative paths resolve against the project root, not the config dir.
    assert config.data_dir.name == "data"


def test_load_config_missing_app_yaml_raises(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    with open(config_dir / "study.yaml", "w") as f:
        yaml.safe_dump({"study_version": "x"}, f)

    with pytest.raises(ConfigError):
        load_config(config_dir)


def test_load_config_invalid_mode_raises(tmp_path: Path) -> None:
    config_dir = _write_config_dir(tmp_path, app_overrides={"mode": "NOT_A_MODE"})
    with pytest.raises(ConfigError):
        load_config(config_dir)


def test_load_config_missing_required_path_raises(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    with open(config_dir / "app.yaml", "w") as f:
        yaml.safe_dump({"app": {"mode": "DEVELOPMENT"}, "paths": {"data_dir": "data"}}, f)
    with open(config_dir / "study.yaml", "w") as f:
        yaml.safe_dump({"study_version": "x"}, f)

    with pytest.raises(ConfigError):
        load_config(config_dir)
