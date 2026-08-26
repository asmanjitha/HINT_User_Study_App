from __future__ import annotations

import time
from pathlib import Path

from core.database import Database
from core.workflow_manager import (
    STUDY1_REQUIRED_CONDITION_COUNT,
    STUDY1_REQUIRED_MODALITIES,
    STUDY1_REQUIRED_TIMINGS,
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
    db = Database(
        tmp_path / "identifiable.sqlite3",
        tmp_path / "experimental.sqlite3",
    )
    manager = WorkflowManager(db, session_manager=None, event_bus=None)  # type: ignore[arg-type]
    return manager, db


def _insert_run(
    db: Database,
    *,
    step: WorkflowStep,
    run_id: str,
    practice: bool,
) -> None:
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
            step.value,
            Study.STUDY_1.value,
            int(practice),
            StepRunStatus.COMPLETED.value,
            f"P001_{run_id}",
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
    timing: FeedbackTiming,
    modality: Modality,
    *,
    practice: bool,
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
            f"P001_S_{trial_id}",
            "P001",
            Study.STUDY_1.value,
            Environment.GRIDWORLD.value,
            timing.value,
            modality.value,
            int(practice),
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


def _complete_matrix(db: Database, *, practice: bool, prefix: str) -> None:
    n = 1
    for timing in STUDY1_REQUIRED_TIMINGS:
        for modality in STUDY1_REQUIRED_MODALITIES:
            _insert_trial(
                db,
                f"{prefix}{n:02d}",
                timing,
                modality,
                practice=practice,
            )
            n += 1


def test_training_has_its_own_eight_condition_matrix(tmp_path: Path) -> None:
    manager, _ = _manager(tmp_path)
    statuses = manager.study1_condition_statuses("P001", practice=True)
    assert len(statuses) == STUDY1_REQUIRED_CONDITION_COUNT
    assert all(item.status == "Not Started" for item in statuses)


def test_training_and_study_condition_status_are_independent(tmp_path: Path) -> None:
    manager, db = _manager(tmp_path)
    _insert_trial(
        db,
        "TR01",
        FeedbackTiming.ANYTIME,
        Modality.VOICE,
        practice=True,
    )

    training = manager.study1_condition_status(
        "P001",
        FeedbackTiming.ANYTIME,
        Modality.VOICE,
        practice=True,
    )
    study = manager.study1_condition_status(
        "P001",
        FeedbackTiming.ANYTIME,
        Modality.VOICE,
        practice=False,
    )

    assert training.status == "Completed"
    assert study.status == "Not Started"


def test_seven_of_eight_training_conditions_keeps_training_in_progress(tmp_path: Path) -> None:
    manager, db = _manager(tmp_path)
    _insert_run(
        db,
        step=WorkflowStep.STUDY1_TRAINING,
        run_id="P001_S1TR_01",
        practice=True,
    )

    conditions = [
        (timing, modality)
        for timing in STUDY1_REQUIRED_TIMINGS
        for modality in STUDY1_REQUIRED_MODALITIES
    ]
    for n, (timing, modality) in enumerate(conditions[:7], start=1):
        _insert_trial(
            db,
            f"TR{n:02d}",
            timing,
            modality,
            practice=True,
        )

    summary = manager.step_status("P001", WorkflowStep.STUDY1_TRAINING)
    assert summary.completed_count == 7
    assert summary.overall_status == StepOverallStatus.IN_PROGRESS


def test_all_eight_training_conditions_complete_training_only(tmp_path: Path) -> None:
    manager, db = _manager(tmp_path)
    _insert_run(
        db,
        step=WorkflowStep.STUDY1_TRAINING,
        run_id="P001_S1TR_01",
        practice=True,
    )
    _complete_matrix(db, practice=True, prefix="TR")

    training = manager.step_status("P001", WorkflowStep.STUDY1_TRAINING)
    study = manager.study1_condition_statuses("P001", practice=False)

    assert training.completed_count == 8
    assert training.overall_status == StepOverallStatus.COMPLETED
    assert manager.next_incomplete_study1_condition("P001", practice=True) is None
    assert all(item.status == "Not Started" for item in study)
