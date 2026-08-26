from __future__ import annotations

import time
from pathlib import Path

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
    StepRunStatus,
    Study,
    TrialStatus,
    WorkflowStep,
)


def _manager(tmp_path: Path) -> tuple[WorkflowManager, Database]:
    db = Database(tmp_path / "identifiable.sqlite3", tmp_path / "experimental.sqlite3")
    manager = WorkflowManager(db, session_manager=None, event_bus=None)  # type: ignore[arg-type]
    return manager, db


def _insert_run(db: Database) -> None:
    now = time.time()
    db.experimental_conn.execute(
        """
        INSERT INTO workflow_runs
        (run_id, participant_code, step, study, practice, status,
         session_id, trial_id, created_at, started_at, ended_at, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "P001_S2ST_01",
            "P001",
            WorkflowStep.STUDY2_STUDY.value,
            Study.STUDY_2.value,
            0,
            StepRunStatus.COMPLETED.value,
            "P001_S20",
            None,
            now,
            now,
            now,
            "",
        ),
    )
    db.experimental_conn.commit()


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
            trial_id,
            "P001_S20",
            "P001",
            Study.STUDY_2.value,
            Environment.GRIDWORLD.value,
            timing.value,
            modality.value,
            0,
            TrialStatus.COMPLETED.value,
            42,
            None,
            now,
            now,
            now,
            None,
        ),
    )
    db.experimental_conn.commit()


def test_study2_focuses_on_four_gridworld_modalities() -> None:
    assert STUDY2_REQUIRED_CONDITION_COUNT == 4
    assert STUDY2_REQUIRED_MODALITIES == (
        Modality.KEYBOARD,
        Modality.JOYSTICK,
        Modality.VOICE,
        Modality.EYE_GAZE,
    )


def test_modality_completion_is_independent_of_requested_vs_anytime(tmp_path: Path) -> None:
    manager, db = _manager(tmp_path)
    _insert_trial(db, "V1", Modality.VOICE, FeedbackTiming.ANYTIME)
    voice = manager.study2_condition_status("P001", Modality.VOICE)
    assert voice.status == "Completed"
    assert voice.last_feedback_timing == FeedbackTiming.ANYTIME


def test_all_modalities_complete_study2_step(tmp_path: Path) -> None:
    manager, db = _manager(tmp_path)
    _insert_run(db)
    for i, modality in enumerate(STUDY2_REQUIRED_MODALITIES, start=1):
        timing = FeedbackTiming.REQUESTED if i % 2 else FeedbackTiming.ANYTIME
        _insert_trial(db, f"M{i}", modality, timing)

    summary = manager.step_status("P001", WorkflowStep.STUDY2_STUDY)
    assert summary.completed_count == 4
    assert summary.overall_status == StepOverallStatus.COMPLETED
    assert manager.next_incomplete_study2_condition("P001") is None


def test_legacy_implicit_row_counts_as_eye_gaze_completion(tmp_path: Path) -> None:
    manager, db = _manager(tmp_path)
    _insert_trial(db, "G_LEGACY", Modality.IMPLICIT, FeedbackTiming.REQUESTED)

    gaze = manager.study2_condition_status("P001", Modality.EYE_GAZE)
    assert gaze.status == "Completed"
    assert gaze.completed_trials == 1
    assert gaze.last_trial_id == "G_LEGACY"
