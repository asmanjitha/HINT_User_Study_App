from __future__ import annotations

from pathlib import Path

from core.database import Database
from core.event_bus import EventBus
from core.trial_manager import TrialManager
from core.workflow_manager import WorkflowManager
from models.enums import (
    CollectionRunStatus,
    Environment,
    FeedbackTiming,
    Modality,
    Study,
)
from models.session import Session
from models.trial import ExperimentCondition


def _db(tmp_path: Path) -> Database:
    return Database(tmp_path / "identifiable.sqlite3", tmp_path / "experimental.sqlite3")


def _session(tmp_path: Path) -> Session:
    session_dir = tmp_path / "data" / "P001" / "S01"
    session_dir.mkdir(parents=True)
    return Session(
        session_id="P001_S01",
        participant_code="P001",
        study=Study.COMBINED_SESSION,
        session_dir=str(session_dir),
    )


def _condition(
    *,
    study: Study = Study.STUDY_1,
    timing: FeedbackTiming = FeedbackTiming.ANYTIME,
    modality: Modality = Modality.JOYSTICK,
) -> ExperimentCondition:
    return ExperimentCondition(
        study=study,
        environment=Environment.GRIDWORLD,
        feedback_timing=timing,
        modality=modality,
    )


def test_same_condition_reuses_t_code_and_increments_run(tmp_path: Path) -> None:
    db = _db(tmp_path)
    manager = TrialManager(db, EventBus())
    session = _session(tmp_path)
    condition = _condition()

    first = manager.create_trial(session, condition, practice=False)
    manager.end_trial(
        first,
        completed=True,
        collection_status=CollectionRunStatus.INVALID,
        repeat_reason="Participant mistake",
    )
    second = manager.create_trial(session, condition, practice=False)

    assert first.condition_code == "T01"
    assert first.run_code == "R01"
    assert second.condition_code == "T01"
    assert second.run_code == "R02"
    assert first.trial_id == "P001_S01_ST1ST_T01_R01"
    assert second.trial_id == "P001_S01_ST1ST_T01_R02"

    expected = (
        Path(session.session_dir)
        / "Study1_ExplicitFeedback"
        / "T01_Gridworld_Anytime_Joystick"
    )
    assert first.trial_path == expected / "R01"
    assert second.trial_path == expected / "R02"


def test_different_condition_gets_next_t_code(tmp_path: Path) -> None:
    db = _db(tmp_path)
    manager = TrialManager(db, EventBus())
    session = _session(tmp_path)

    first = manager.create_trial(session, _condition(), practice=False)
    second = manager.create_trial(
        session,
        _condition(timing=FeedbackTiming.REQUESTED, modality=Modality.KEYBOARD),
        practice=False,
    )

    assert first.condition_code == "T01"
    assert second.condition_code == "T02"
    assert second.run_code == "R01"
    assert second.trial_path is not None
    assert "T02_Gridworld_Requested_Keyboard" in str(second.trial_path)


def test_study2_has_its_own_t_numbering(tmp_path: Path) -> None:
    db = _db(tmp_path)
    manager = TrialManager(db, EventBus())
    session = _session(tmp_path)

    manager.create_trial(session, _condition(study=Study.STUDY_1), practice=False)
    study2 = manager.create_trial(
        session,
        _condition(
            study=Study.STUDY_2,
            timing=FeedbackTiming.REQUESTED,
            modality=Modality.VOICE,
        ),
        practice=False,
    )

    assert study2.condition_code == "T01"
    assert study2.run_code == "R01"
    assert study2.trial_path is not None
    assert study2.trial_path == (
        Path(session.session_dir)
        / "Study2_FeedbackModality"
        / "T01_Gridworld_Requested_Voice"
        / "R01"
    )


def test_training_uses_tr_prefix_and_training_folder(tmp_path: Path) -> None:
    db = _db(tmp_path)
    manager = TrialManager(db, EventBus())
    session = _session(tmp_path)

    trial = manager.create_trial(
        session,
        _condition(timing=FeedbackTiming.REQUESTED, modality=Modality.KEYBOARD),
        practice=True,
    )

    assert trial.condition_code == "TR01"
    assert trial.run_code == "R01"
    assert trial.trial_path == (
        Path(session.session_dir)
        / "Training"
        / "Study1"
        / "TR01_Gridworld_Requested_Keyboard"
        / "R01"
    )


def test_invalid_attempt_does_not_complete_study_condition(tmp_path: Path) -> None:
    db = _db(tmp_path)
    trial_manager = TrialManager(db, EventBus())
    session = _session(tmp_path)
    condition = _condition(
        study=Study.STUDY_1,
        timing=FeedbackTiming.ANYTIME,
        modality=Modality.KEYBOARD,
    )

    invalid = trial_manager.create_trial(session, condition, practice=False)
    trial_manager.end_trial(
        invalid,
        completed=True,
        collection_status=CollectionRunStatus.INVALID,
        repeat_reason="Participant mistake",
    )

    workflow = WorkflowManager(db, session_manager=None, event_bus=None)  # type: ignore[arg-type]
    status = workflow.study1_study_condition_status("P001", "grid_anytime")
    assert status.status == "Needs Repeat"

    valid = trial_manager.create_trial(session, condition, practice=False)
    trial_manager.end_trial(
        valid,
        completed=True,
        collection_status=CollectionRunStatus.VALID,
    )
    status = workflow.study1_study_condition_status("P001", "grid_anytime")
    assert status.status == "Completed"
    assert valid.condition_code == invalid.condition_code
    assert valid.run_code == "R02"
