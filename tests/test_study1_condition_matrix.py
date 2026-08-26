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
            run_id,
            "P001",
            WorkflowStep.STUDY1_STUDY.value,
            Study.STUDY_1.value,
            0,
            StepRunStatus.COMPLETED.value,
            "P001_S01",
            None,
            now,
            now,
            now,
            "",
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
            trial_id,
            "P001_S01",
            "P001",
            Study.STUDY_1.value,
            environment.value,
            timing.value,
            modality.value,
            0,
            status.value,
            42,
            None,
            now,
            now,
            now if status in (TrialStatus.COMPLETED, TrialStatus.STOPPED) else None,
            None,
        ),
    )
    db.experimental_conn.commit()


def test_study1_protocol_has_four_required_conditions_in_three_main_settings() -> None:
    assert STUDY1_STUDY_REQUIRED_CONDITION_COUNT == 4
    assert [c.environment for c in STUDY1_STUDY_REQUIRED_CONDITIONS].count(Environment.GRIDWORLD) == 2
    assert any(c.environment == Environment.CONTINUOUS_ROOM for c in STUDY1_STUDY_REQUIRED_CONDITIONS)
    assert any(c.environment == Environment.HUMAN_AGENT_BASELINE for c in STUDY1_STUDY_REQUIRED_CONDITIONS)


def test_new_participant_has_four_not_started_study1_conditions(tmp_path: Path) -> None:
    manager, _ = _manager(tmp_path)
    statuses = manager.study1_study_condition_statuses("P001")
    assert len(statuses) == 4
    assert all(item.status == "Not Started" for item in statuses)


def test_gridworld_study1_accepts_only_explicit_keyboard_or_joystick(tmp_path: Path) -> None:
    manager, db = _manager(tmp_path)
    _insert_trial(
        db,
        "VOICE01",
        Environment.GRIDWORLD,
        FeedbackTiming.REQUESTED,
        Modality.VOICE,
    )
    requested = manager.study1_study_condition_status("P001", "grid_requested")
    assert requested.status == "Not Started"

    _insert_trial(
        db,
        "JOY01",
        Environment.GRIDWORLD,
        FeedbackTiming.REQUESTED,
        Modality.JOYSTICK,
    )
    requested = manager.study1_study_condition_status("P001", "grid_requested")
    assert requested.status == "Completed"
    assert requested.last_modality == Modality.JOYSTICK


def test_room_condition_accepts_explicit_feedback_with_either_timing(tmp_path: Path) -> None:
    manager, db = _manager(tmp_path)
    _insert_trial(
        db,
        "ROOM01",
        Environment.CONTINUOUS_ROOM,
        FeedbackTiming.ANYTIME,
        Modality.KEYBOARD,
    )
    room = manager.study1_study_condition_status("P001", "room_navigation")
    assert room.status == "Completed"
    assert room.last_feedback_timing == FeedbackTiming.ANYTIME


def test_baseline_requires_no_participant_feedback_marker(tmp_path: Path) -> None:
    manager, db = _manager(tmp_path)
    _insert_trial(
        db,
        "BASE_BAD",
        Environment.HUMAN_AGENT_BASELINE,
        FeedbackTiming.REQUESTED,
        Modality.KEYBOARD,
    )
    assert manager.study1_study_condition_status("P001", "baseline_navigation").status == "Not Started"

    _insert_trial(
        db,
        "BASE_OK",
        Environment.HUMAN_AGENT_BASELINE,
        FeedbackTiming.NOT_APPLICABLE,
        Modality.NONE,
    )
    assert manager.study1_study_condition_status("P001", "baseline_navigation").status == "Completed"


def test_all_four_protocol_conditions_complete_study1(tmp_path: Path) -> None:
    manager, db = _manager(tmp_path)
    _insert_workflow_run(db)
    _insert_trial(db, "T1", Environment.GRIDWORLD, FeedbackTiming.REQUESTED, Modality.KEYBOARD)
    _insert_trial(db, "T2", Environment.GRIDWORLD, FeedbackTiming.ANYTIME, Modality.KEYBOARD)
    _insert_trial(db, "T3", Environment.CONTINUOUS_ROOM, FeedbackTiming.REQUESTED, Modality.JOYSTICK)
    _insert_trial(db, "T4", Environment.HUMAN_AGENT_BASELINE, FeedbackTiming.NOT_APPLICABLE, Modality.NONE)

    summary = manager.step_status("P001", WorkflowStep.STUDY1_STUDY)
    assert summary.completed_count == 4
    assert summary.overall_status == StepOverallStatus.COMPLETED
    assert manager.next_incomplete_study1_study_condition("P001") is None
