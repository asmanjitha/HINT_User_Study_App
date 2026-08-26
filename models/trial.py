"""Trial and experimental-condition models."""

from __future__ import annotations

import time

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from models.enums import (
    CollectionRunStatus,
    Environment,
    FeedbackTiming,
    Modality,
    Study,
    TrialStatus,
)


@dataclass
class ExperimentCondition:
    study: Study
    environment: Environment
    feedback_timing: FeedbackTiming
    modality: Modality

    rl_algorithm: str = "actor_critic_gridworld"

    order_index: Optional[int] = None
    random_seed: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "study": self.study.value,
            "environment": self.environment.value,
            "feedback_timing": self.feedback_timing.value,
            "modality": self.modality.value,
            "rl_algorithm": self.rl_algorithm,
            "order_index": self.order_index,
            "random_seed": self.random_seed,
        }


@dataclass
class Trial:
    """One concrete R## attempt of one stable T##/TR## condition."""

    trial_id: str
    session_id: str
    participant_code: str

    condition: ExperimentCondition

    practice: bool = False
    status: TrialStatus = TrialStatus.CREATED
    collection_status: CollectionRunStatus = CollectionRunStatus.PENDING

    # Human-readable storage identity.
    condition_code: str = ""
    run_code: str = ""
    condition_name: str = ""
    repeat_reason: str = ""

    created_at: float = field(default_factory=time.time)

    started_at: Optional[float] = None
    ended_at: Optional[float] = None

    trial_dir: Optional[str] = None

    @property
    def trial_path(self) -> Optional[Path]:
        return Path(self.trial_dir) if self.trial_dir else None

    @property
    def readable_run_label(self) -> str:
        if self.condition_code and self.run_code:
            return f"{self.condition_code} / {self.run_code}"
        return self.trial_id

    def to_metadata_dict(self) -> dict:
        return {
            "trial_id": self.trial_id,
            "session_id": self.session_id,
            "participant_code": self.participant_code,
            "condition_code": self.condition_code,
            "run_code": self.run_code,
            "condition_name": self.condition_name,
            "collection_status": self.collection_status.value,
            "repeat_reason": self.repeat_reason,
            "practice": self.practice,
            "status": self.status.value,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "trial_dir": self.trial_dir,
            "condition": self.condition.to_dict(),
        }
