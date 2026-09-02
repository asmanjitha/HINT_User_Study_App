from __future__ import annotations

import time
from pathlib import Path

from core.database import Database
from core.workflow_manager import (
    STUDY1_STUDY_REQUIRED_CONDITIONS,
    STUDY1_STUDY_REQUIRED_CONDITION_COUNT,
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


def _insert_workflow_run(db: Database, run_id: str = "P001_S1ST_01") -> None:
    now = time.time()
    db.experimental_conn.execute(
        """
        INSERT INTO workflow_runs
        (run_id, participant_code, step, study, practice, status,
         session_id, trial_id, created_at, started_at, ended_at, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id, "P001", WorkflowStep.STUDY1_STUDY.value, Study.STUDY_1.value,
            0, StepRunStatus.COMPLETED.value, "P001_S01", None,
            now, now, now, "",
        ),
    )
    db.experimental_conn.commit()


def _insert_trial(
    db: Database,
    trial_id: str,
    environment: Environment,
    timing: FeedbackTiming,
    modality: Modality,
    status: TrialStatus = TrialStatus.COMPLETED,
) -> None:
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
            trial_id, "P001_S01", "P001", Study.STUDY_1.value,
            environment.value, timing.value, modality.value, 0, status.value,
            42, None, now, now,
            now if status in (TrialStatus.COMPLETED, TrialStatus.STOPPED) else None,
            None,
        ),
    )
    db.experimental_conn.commit()


def test_study1_protocol_is_four_keyboard_conditions_across_two_environments() -> None:
    assert STUDY1_STUDY_REQUIRED_CONDITION_COUNT == 4
    assert [c.environment for c in STUDY1_STUDY_REQUIRED_CONDITIONS].count(Environment.GRIDWORLD) == 2
    assert [c.environment for c in STUDY1_STUDY_REQUIRED_CONDITIONS].count(Environment.CONTINUOUS_ROOM) == 2
    assert all(c.allowed_modalities == (Modality.KEYBOARD,) for c in STUDY1_STUDY_REQUIRED_CONDITIONS)
    assert {c.feedback_timing for c in STUDY1_STUDY_REQUIRED_CONDITIONS} == {
        FeedbackTiming.REQUESTED,
        FeedbackTiming.ANYTIME,
    }
    assert all(c.environment != Environment.HUMAN_AGENT_BASELINE for c in STUDY1_STUDY_REQUIRED_CONDITIONS)


def test_new_participant_has_four_not_started_study1_conditions(tmp_path: Path) -> None:
    manager, _ = _manager(tmp_path)
    statuses = manager.study1_study_condition_statuses("P001")
    assert len(statuses) == 4
    assert all(item.status == "Not Started" for item in statuses)


def test_study1_counts_keyboard_only(tmp_path: Path) -> None:
    manager, db = _manager(tmp_path)
    _insert_trial(db, "VOICE01", Environment.GRIDWORLD, FeedbackTiming.REQUESTED, Modality.VOICE)
    _insert_trial(db, "JOY01", Environment.GRIDWORLD, FeedbackTiming.REQUESTED, Modality.JOYSTICK)
    assert manager.study1_study_condition_status("P001", "grid_requested").status == "Not Started"

    _insert_trial(db, "KEY01", Environment.GRIDWORLD, FeedbackTiming.REQUESTED, Modality.KEYBOARD)
    requested = manager.study1_study_condition_status("P001", "grid_requested")
    assert requested.status == "Completed"
    assert requested.last_modality == Modality.KEYBOARD


def test_continuous_requested_and_anytime_are_separate_required_conditions(tmp_path: Path) -> None:
    manager, db = _manager(tmp_path)
    _insert_trial(db, "ROOM_ANYTIME", Environment.CONTINUOUS_ROOM, FeedbackTiming.ANYTIME, Modality.KEYBOARD)
    assert manager.study1_study_condition_status("P001", "room_anytime").status == "Completed"
    assert manager.study1_study_condition_status("P001", "room_requested").status == "Not Started"

    _insert_trial(db, "ROOM_REQUESTED", Environment.CONTINUOUS_ROOM, FeedbackTiming.REQUESTED, Modality.KEYBOARD)
    assert manager.study1_study_condition_status("P001", "room_requested").status == "Completed"


def test_baseline_trial_does_not_count_toward_revised_study1(tmp_path: Path) -> None:
    manager, db = _manager(tmp_path)
    _insert_trial(
        db, "OLD_BASELINE", Environment.HUMAN_AGENT_BASELINE,
        FeedbackTiming.NOT_APPLICABLE, Modality.NONE,
    )
    statuses = manager.study1_study_condition_statuses("P001")
    assert all(item.status == "Not Started" for item in statuses)


def test_all_four_protocol_conditions_complete_study1(tmp_path: Path) -> None:
    manager, db = _manager(tmp_path)
    _insert_workflow_run(db)
    _insert_trial(db, "T1", Environment.GRIDWORLD, FeedbackTiming.REQUESTED, Modality.KEYBOARD)
    _insert_trial(db, "T2", Environment.GRIDWORLD, FeedbackTiming.ANYTIME, Modality.KEYBOARD)
    _insert_trial(db, "T3", Environment.CONTINUOUS_ROOM, FeedbackTiming.REQUESTED, Modality.KEYBOARD)
    _insert_trial(db, "T4", Environment.CONTINUOUS_ROOM, FeedbackTiming.ANYTIME, Modality.KEYBOARD)

    summary = manager.step_status("P001", WorkflowStep.STUDY1_STUDY)
    assert summary.completed_count == 4
    assert summary.overall_status == StepOverallStatus.COMPLETED
    assert manager.next_incomplete_study1_study_condition("P001") is None
