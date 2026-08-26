"""Workflow step-run model.

A ``StepRun`` records one attempt at one :class:`~models.enums.WorkflowStep`
for one participant (e.g. "P004's 2nd Study 1 Training run"). Steps are
allowed to be repeated -- a participant may need to redo training, or do
the HIL-RL study itself more than once -- so a step's overall status is an
aggregate over all of its runs (see ``core/workflow_manager.py``), not a
single field.

Each run is backed by a real ``Session`` (created via ``SessionManager``,
same as the rest of the app), so all existing session-centric folder/DB
behavior is reused rather than duplicated.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from models.enums import StepRunStatus, Study, WorkflowStep


@dataclass
class StepRun:
    run_id: str
    """e.g. 'P004_S1TR_01'."""

    participant_code: str
    step: WorkflowStep
    study: Optional[Study]
    practice: bool

    status: StepRunStatus = StepRunStatus.IN_PROGRESS

    session_id: Optional[str] = None
    trial_id: Optional[str] = None

    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    ended_at: Optional[float] = None

    notes: str = ""

    def to_metadata_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "participant_code": self.participant_code,
            "step": self.step.value,
            "study": self.study.value if self.study else None,
            "practice": self.practice,
            "status": self.status.value,
            "session_id": self.session_id,
            "trial_id": self.trial_id,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "notes": self.notes,
        }
