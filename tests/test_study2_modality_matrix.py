from __future__ import annotations

import time
from pathlib import Path

import pytest

from core.database import Database
from core.workflow_manager import (
    STUDY2_REQUIRED_CONDITION_COUNT,
    STUDY2_REQUIRED_MODALITIES,
    WorkflowManager,
)
from models.enums import (
    Environment,
    FeedbackTiming,
    Modality,
    StepOverallStatus,
    Study,
    TrialStatus,
    WorkflowStep,
)


class _FakeSessionManager:
    def get_or_create_active_session(self, participant_code: str):
        from models.session import Session
        return Session(
            session_id=f"{participant_code}_S01",
            participant_code=participant_code,
            study=Study.COMBINED_SESSION,
        )


def _manager(tmp_path: Path) -> tuple[WorkflowManager, Database]:
    db = Database(tmp_path / "identifiable.sqlite3", tmp_path / "experimental.sqlite3")
    manager = WorkflowManager(db, session_manager=_FakeSessionManager(), event_bus=None)  # type: ignore[arg-type]
    return manager, db


def _insert_trial(db: Database, trial_id: str, modality: Modality, timing: FeedbackTiming) -> None:
    now = time.time()
    db.experimental_conn.execute(
        """
        INSERT INTO trials
        (trial_id, session_id, participant_code, study, environment,
         feedback_timing, modality, practice, status, random_seed,
         order_index, created_at, started_at, ended_at, trial_dir)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            trial_id, "P001_S01", "P001", Study.STUDY_2.value,
            Environment.GRIDWORLD.value, timing.value, modality.value, 0,
            TrialStatus.COMPLETED.value, 42, None, now, now, now, None,
        ),
    )
    db.experimental_conn.commit()


def test_study2_has_three_selectable_gridworld_modalities() -> None:
    assert STUDY2_REQUIRED_CONDITION_COUNT == 3
    assert STUDY2_REQUIRED_MODALITIES == (
        Modality.KEYBOARD,
        Modality.JOYSTICK,
        Modality.VOICE,
    )
    assert Modality.EYE_GAZE not in STUDY2_REQUIRED_MODALITIES


def test_modality_completion_is_independent_of_requested_vs_anytime(tmp_path: Path) -> None:
    manager, db = _manager(tmp_path)
    _insert_trial(db, "V1", Modality.VOICE, FeedbackTiming.ANYTIME)
    voice = manager.study2_condition_status("P001", Modality.VOICE)
    assert voice.status == "Completed"
    assert voice.last_feedback_timing == FeedbackTiming.ANYTIME


def test_all_three_modalities_auto_complete_study2_step(tmp_path: Path) -> None:
    manager, db = _manager(tmp_path)
    for i, modality in enumerate(STUDY2_REQUIRED_MODALITIES, start=1):
        timing = FeedbackTiming.REQUESTED if i % 2 else FeedbackTiming.ANYTIME
        _insert_trial(db, f"M{i}", modality, timing)

    summary = manager.step_status("P001", WorkflowStep.STUDY2_STUDY)
    assert summary.completed_count == 3
    assert summary.overall_status == StepOverallStatus.COMPLETED
    assert manager.next_incomplete_study2_condition("P001") is None


def test_experimenter_can_finish_study2_after_one_selected_modality(tmp_path: Path) -> None:
    manager, db = _manager(tmp_path)
    _insert_trial(db, "K1", Modality.KEYBOARD, FeedbackTiming.REQUESTED)
    before = manager.step_status("P001", WorkflowStep.STUDY2_STUDY)
    assert before.completed_count == 1
    assert before.overall_status == StepOverallStatus.IN_PROGRESS

    marker = manager.finish_study2("P001")
    assert marker.notes == "STUDY2_FINISHED_BY_EXPERIMENTER"
    assert manager.study2_finished("P001") is True

    after = manager.step_status("P001", WorkflowStep.STUDY2_STUDY)
    assert after.completed_count == 1
    assert after.overall_status == StepOverallStatus.COMPLETED


def test_eye_gaze_and_legacy_implicit_are_not_study2_modalities(tmp_path: Path) -> None:
    manager, db = _manager(tmp_path)
    _insert_trial(db, "OLD_GAZE", Modality.EYE_GAZE, FeedbackTiming.REQUESTED)
    _insert_trial(db, "OLD_IMPLICIT", Modality.IMPLICIT, FeedbackTiming.REQUESTED)

    assert all(item.status == "Not Started" for item in manager.study2_condition_statuses("P001"))
    with pytest.raises(ValueError):
        manager.study2_condition_status("P001", Modality.EYE_GAZE)
