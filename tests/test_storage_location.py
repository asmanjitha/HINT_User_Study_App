from __future__ import annotations

from pathlib import Path

import yaml

from core.config_loader import load_config
from core.storage_location import (
    load_storage_location,
    save_storage_location,
    validate_storage_location,
)


def _write_base_config(config_dir: Path) -> None:
    config_dir.mkdir()
    with open(config_dir / "app.yaml", "w", encoding="utf-8") as stream:
        yaml.safe_dump(
            {
                "app": {"mode": "DEVELOPMENT"},
                "paths": {
                    "data_dir": "data",
                    "logs_dir": "logs",
                    "identifiable_db": "data/identifiable.sqlite3",
                    "experimental_db": "data/experimental.sqlite3",
                },
                "backup": {"destination": ""},
            },
            stream,
        )
    with open(config_dir / "study.yaml", "w", encoding="utf-8") as stream:
        yaml.safe_dump({"study_version": "TEST"}, stream)


def test_custom_storage_root_moves_data_databases_and_logs(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    _write_base_config(config_dir)
    external = tmp_path / "external_drive" / "HINT_Data"
    ok, _message, free_bytes = validate_storage_location(external, create=True)
    assert ok and free_bytes is not None

    save_storage_location(config_dir, external, prompt_on_startup=False)
    settings = load_storage_location(config_dir)
    config = load_config(config_dir)

    assert settings.prompt_on_startup is False
    assert config.data_dir == external.resolve()
    assert config.identifiable_db == external.resolve() / "identifiable.sqlite3"
    assert config.experimental_db == external.resolve() / "experimental.sqlite3"
    assert config.logs_dir == external.resolve() / "_system_logs"
