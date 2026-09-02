from __future__ import annotations

import time
from pathlib import Path

from core.database import Database
from core.workflow_manager import (
    TRAINING_CONDITIONS,
    TRAINING_REQUIRED_CONDITIONS,
    STUDY1_TRAINING_REQUIRED_CONDITION_COUNT,
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


def _manager(tmp_path: Path, session_manager=None) -> tuple[WorkflowManager, Database]:
    db = Database(tmp_path / "identifiable.sqlite3", tmp_path / "experimental.sqlite3")
    manager = WorkflowManager(db, session_manager=session_manager, event_bus=None)  # type: ignore[arg-type]
    return manager, db


def _insert_run(db: Database, run_id: str = "P001_S1TR_01") -> None:
    now = time.time()
    db.experimental_conn.execute(
        """
        INSERT INTO workflow_runs
        (run_id, participant_code, step, study, practice, status,
         session_id, trial_id, created_at, started_at, ended_at, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id, "P001", WorkflowStep.STUDY1_TRAINING.value, Study.STUDY_1.value,
            1, StepRunStatus.COMPLETED.value, "P001_S01", None,
            now, now, now, "",
        ),
    )
    db.experimental_conn.commit()


def _insert_training_trial(db: Database, trial_id: str, req, *, practice: bool = True) -> None:
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
            trial_id, "P001_S01", "P001", req.study.value, req.environment.value,
            req.feedback_timing.value, req.modality.value, int(practice),
            TrialStatus.COMPLETED.value, 42, None, now, now, now, None,
        ),
    )
    db.experimental_conn.commit()


def test_training_has_six_required_conditions_plus_optional_hololens_last() -> None:
    assert STUDY1_TRAINING_REQUIRED_CONDITION_COUNT == 6
    assert len(TRAINING_CONDITIONS) == 7
    assert len(TRAINING_REQUIRED_CONDITIONS) == 6
    optional = TRAINING_CONDITIONS[-1]
    assert optional.required is False
    assert "HoloLens" in optional.label
    assert optional.modality == Modality.NONE


def test_training_adds_continuous_requested_and_anytime_keyboard() -> None:
    room = [c for c in TRAINING_REQUIRED_CONDITIONS if c.environment == Environment.CONTINUOUS_ROOM]
    assert len(room) == 2
    assert {c.feedback_timing for c in room} == {FeedbackTiming.REQUESTED, FeedbackTiming.ANYTIME}
    assert all(c.modality == Modality.KEYBOARD for c in room)


def test_anytime_training_is_keyboard_only() -> None:
    anytime = [c for c in TRAINING_REQUIRED_CONDITIONS if c.feedback_timing == FeedbackTiming.ANYTIME]
    assert anytime
    assert all(c.modality == Modality.KEYBOARD for c in anytime)
    assert all(
        c.feedback_timing == FeedbackTiming.REQUESTED
        for c in TRAINING_REQUIRED_CONDITIONS
        if c.modality in (Modality.JOYSTICK, Modality.VOICE)
    )


def test_training_and_nonpractice_trials_are_independent(tmp_path: Path) -> None:
    manager, db = _manager(tmp_path)
    req = TRAINING_REQUIRED_CONDITIONS[0]
    _insert_training_trial(db, "TR01", req, practice=True)
    _insert_training_trial(db, "ST01", req, practice=False)

    training = manager.training_condition_status("P001", req.key)
    assert training.status == "Completed"
    assert training.completed_trials == 1


def test_five_of_six_required_training_conditions_keeps_training_in_progress(tmp_path: Path) -> None:
    manager, db = _manager(tmp_path)
    _insert_run(db)
    for i, req in enumerate(TRAINING_REQUIRED_CONDITIONS[:-1], start=1):
        _insert_training_trial(db, f"TR{i:02d}", req)

    summary = manager.step_status("P001", WorkflowStep.STUDY1_TRAINING)
    assert summary.completed_count == 5
    assert summary.overall_status == StepOverallStatus.IN_PROGRESS


def test_all_six_required_complete_even_when_optional_hololens_not_run(tmp_path: Path) -> None:
    manager, db = _manager(tmp_path)
    _insert_run(db)
    for i, req in enumerate(TRAINING_REQUIRED_CONDITIONS, start=1):
        _insert_training_trial(db, f"TR{i:02d}", req)

    summary = manager.step_status("P001", WorkflowStep.STUDY1_TRAINING)
    optional = manager.training_condition_status("P001", TRAINING_CONDITIONS[-1].key)
    assert summary.completed_count == 6
    assert summary.overall_status == StepOverallStatus.COMPLETED
    assert optional.status == "Not Started"
    assert manager.next_incomplete_training_condition("P001") is None


def test_quick_pass_marks_required_training_only_without_fake_trials(tmp_path: Path) -> None:
    class _FakeSessionManager:
        def get_or_create_active_session(self, participant_code: str):
            from models.session import Session
            return Session(
                session_id=f"{participant_code}_S01",
                participant_code=participant_code,
                study=Study.COMBINED_SESSION,
            )

    manager, db = _manager(tmp_path, _FakeSessionManager())
    run = manager.quick_pass_study1_training("P001")

    assert run.status == StepRunStatus.COMPLETED
    statuses = manager.training_condition_statuses("P001")
    required = [x for x in statuses if x.required]
    optional = [x for x in statuses if not x.required]
    assert len(required) == 6
    assert all(x.status == "Completed" for x in required)
    assert all(x.completed_trials == 0 for x in required)
    assert len(optional) == 1 and optional[0].status == "Not Started"

    summary = manager.step_status("P001", WorkflowStep.STUDY1_TRAINING)
    assert summary.completed_count == 6
    assert summary.overall_status == StepOverallStatus.COMPLETED
    assert db.experimental_conn.execute("SELECT COUNT(*) AS n FROM trials").fetchone()["n"] == 0

    same = manager.quick_pass_study1_training("P001")
    assert same.run_id == run.run_id
