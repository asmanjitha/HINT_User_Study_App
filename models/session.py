"""Participant collection-session model.

A session is one collection visit/continuation for a participant and may contain
both Study 1 and Study 2 data. The internal database ID remains P001_S01 while
its on-disk folder is the shorter, readable ``P001/S01``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from models.enums import SessionStatus, Study


@dataclass
class Session:
    session_id: str
    """Internal ID, e.g. ``P023_S01``."""

    participant_code: str
    study: Study = Study.COMBINED_SESSION
    status: SessionStatus = SessionStatus.CREATED
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    ended_at: Optional[float] = None

    session_max_minutes: int = 60
    continuous_task_max_minutes: int = 20

    session_dir: Optional[str] = None
    modality_order: Optional[str] = None
    notes: str = ""

    def to_metadata_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "participant_code": self.participant_code,
            "scope": self.study.value,
            "status": self.status.value,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "session_max_minutes": self.session_max_minutes,
            "continuous_task_max_minutes": self.continuous_task_max_minutes,
            "modality_order": self.modality_order,
            "notes": self.notes,
        }

    @property
    def session_path(self) -> Optional[Path]:
        return Path(self.session_dir) if self.session_dir else None
