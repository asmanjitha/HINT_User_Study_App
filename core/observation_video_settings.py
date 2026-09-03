"""Persistent paths for the two Study 3 observation videos."""

from __future__ import annotations

from pathlib import Path

import yaml


VIDEO_KEYS = ("gridworld", "continuous_room")


def load_observation_video_paths(config_dir: Path) -> dict[str, str]:
    """Load configured video paths without failing application startup."""
    path = Path(config_dir) / "observation_videos.yaml"
    if not path.exists():
        return {key: "" for key in VIDEO_KEYS}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {key: "" for key in VIDEO_KEYS}
    videos = data.get("observation_videos", {}) if isinstance(data, dict) else {}
    return {key: str(videos.get(key, "")).strip() for key in VIDEO_KEYS}


def save_observation_video_paths(config_dir: Path, paths: dict[str, str]) -> Path:
    """Persist both paths so the researcher only needs to select them once."""
    target = Path(config_dir) / "observation_videos.yaml"
    payload = {
        "observation_videos": {
            key: str(paths.get(key, "")).strip() for key in VIDEO_KEYS
        }
    }
    target.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return target


def resolve_observation_video_path(raw_path: str, project_root: Path) -> Path:
    """Resolve absolute paths and project-relative portable paths."""
    path = Path(str(raw_path).strip()).expanduser()
    if not path.is_absolute():
        path = Path(project_root) / path
    return path.resolve()
