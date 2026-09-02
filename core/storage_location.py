"""Persistent selection and validation of the study-data storage root."""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yaml


STORAGE_CONFIG_FILENAME = "storage_location.yaml"


@dataclass(frozen=True)
class StorageLocationSettings:
    data_root: str = ""
    prompt_on_startup: bool = True


def load_storage_location(config_dir: Path) -> StorageLocationSettings:
    path = Path(config_dir) / STORAGE_CONFIG_FILENAME
    if not path.exists():
        return StorageLocationSettings()
    try:
        with open(path, "r", encoding="utf-8") as stream:
            raw = yaml.safe_load(stream) or {}
    except (OSError, yaml.YAMLError):
        return StorageLocationSettings()
    if not isinstance(raw, dict):
        return StorageLocationSettings()
    return StorageLocationSettings(
        data_root=str(raw.get("data_root", "") or "").strip(),
        prompt_on_startup=bool(raw.get("prompt_on_startup", True)),
    )


def save_storage_location(
    config_dir: Path,
    data_root: Path,
    *,
    prompt_on_startup: bool,
) -> None:
    config_dir = Path(config_dir)
    config_dir.mkdir(parents=True, exist_ok=True)
    target = config_dir / STORAGE_CONFIG_FILENAME
    temporary = target.with_suffix(".yaml.tmp")
    payload = {
        "data_root": str(Path(data_root).expanduser().resolve()),
        "prompt_on_startup": bool(prompt_on_startup),
    }
    with open(temporary, "w", encoding="utf-8") as stream:
        yaml.safe_dump(payload, stream, sort_keys=False)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(target)


def configured_data_root(config_dir: Path, project_root: Path) -> Path:
    settings = load_storage_location(config_dir)
    if settings.data_root:
        return Path(settings.data_root).expanduser()
    return Path(project_root) / "data"


def validate_storage_location(
    data_root: Path,
    *,
    create: bool,
) -> tuple[bool, str, int | None]:
    """Verify the selected drive/folder exists, is writable, and has free space."""
    root = Path(data_root).expanduser()
    try:
        if create:
            root.mkdir(parents=True, exist_ok=True)
        if not root.exists():
            return False, "The selected folder is unavailable. Is the external drive connected?", None
        if not root.is_dir():
            return False, "The selected location is not a folder.", None
        with tempfile.NamedTemporaryFile(
            mode="w",
            prefix=".hint_storage_check_",
            dir=root,
            delete=True,
            encoding="utf-8",
        ) as stream:
            stream.write("HINT storage validation")
            stream.flush()
        free_bytes = shutil.disk_usage(root).free
    except OSError as exc:
        return False, f"The selected folder is not writable: {exc}", None
    return True, "Storage folder is available and writable.", free_bytes
