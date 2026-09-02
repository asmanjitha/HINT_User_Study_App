from __future__ import annotations

import time
from pathlib import Path

from core.database import Database
from core.event_bus import EventBus
from core.trial_manager import TrialManager
from core.workflow_manager import (
    OBSERVATION_REQUIRED_CONDITIONS,
    OBSERVATION_REQUIRED_CONDITION_COUNT,
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
from models.session import Session
from models.trial import ExperimentCondition


def _manager(tmp_path: Path) -> tuple[WorkflowManager, Database]:
    db = Database(tmp_path / "identifiable.sqlite3", tmp_path / "experimental.sqlite3")
    return WorkflowManager(db, session_manager=None, event_bus=None), db  # type: ignore[arg-type]


def _insert_observation_trial(db: Database, trial_id: str, environment: Environment) -> None:
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
            trial_id, "P001_S01", "P001", Study.OBSERVATION.value,
            environment.value, FeedbackTiming.NOT_APPLICABLE.value,
            Modality.NONE.value, 0, TrialStatus.COMPLETED.value, 42, None,
            now, now, now, None,
        ),
    )
    db.experimental_conn.commit()


def test_observation_requires_gridworld_and_continuous_without_feedback() -> None:
    assert OBSERVATION_REQUIRED_CONDITION_COUNT == 2
    assert {c.environment for c in OBSERVATION_REQUIRED_CONDITIONS} == {
        Environment.GRIDWORLD,
        Environment.CONTINUOUS_ROOM,
    }


def test_both_observation_environments_complete_final_phase(tmp_path: Path) -> None:
    manager, db = _manager(tmp_path)
    _insert_observation_trial(db, "OBS_GRID", Environment.GRIDWORLD)
    partial = manager.step_status("P001", WorkflowStep.AGENT_OBSERVATION)
    assert partial.completed_count == 1
    assert partial.overall_status == StepOverallStatus.IN_PROGRESS

    _insert_observation_trial(db, "OBS_ROOM", Environment.CONTINUOUS_ROOM)
    done = manager.step_status("P001", WorkflowStep.AGENT_OBSERVATION)
    assert done.completed_count == 2
    assert done.overall_status == StepOverallStatus.COMPLETED
    assert manager.next_incomplete_observation_condition("P001") is None


def test_observation_trials_get_separate_no_feedback_folder(tmp_path: Path) -> None:
    db = Database(tmp_path / "identifiable.sqlite3", tmp_path / "experimental.sqlite3")
    session_dir = tmp_path / "data" / "P001" / "S01"
    session_dir.mkdir(parents=True)
    session = Session(
        session_id="P001_S01",
        participant_code="P001",
        study=Study.COMBINED_SESSION,
        session_dir=str(session_dir),
    )
    manager = TrialManager(db, EventBus())
    condition = ExperimentCondition(
        study=Study.OBSERVATION,
        environment=Environment.GRIDWORLD,
        feedback_timing=FeedbackTiming.NOT_APPLICABLE,
        modality=Modality.NONE,
    )
    trial = manager.create_trial(session, condition, practice=False)

    assert trial.trial_id == "P001_S01_OBS_T01_R01"
    assert trial.trial_path == (
        session_dir
        / "Phase3_AgentObservation_NoFeedback"
        / "T01_Gridworld_NoFeedback"
        / "R01"
    )
