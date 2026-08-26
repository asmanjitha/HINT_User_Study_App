from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.config_loader import AppConfig
from core.database import Database
from core.event_bus import EventBus
from core.session_manager import SessionManager
from models.enums import EventType, SessionStatus, Study


@pytest.fixture()
def config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        raw={"app": {"version": "0.1.0"}},
        mode=__import__("models.enums", fromlist=["AppMode"]).AppMode.DEVELOPMENT,
        data_dir=tmp_path / "data",
        logs_dir=tmp_path / "logs",
        identifiable_db=tmp_path / "data" / "identifiable.sqlite3",
        experimental_db=tmp_path / "data" / "experimental.sqlite3",
        backup_destination="",
        study_raw={"study_version": "TEST_v1", "timing": {"session_max_minutes": 60, "continuous_task_max_minutes": 20}},
        config_dir=tmp_path / "config",
    )


@pytest.fixture()
def db(config: AppConfig) -> Database:
    return Database(config.identifiable_db, config.experimental_db)


@pytest.fixture()
def event_bus() -> EventBus:
    return EventBus()


@pytest.fixture()
def manager(db: Database, config: AppConfig, event_bus: EventBus) -> SessionManager:
    return SessionManager(db, config, event_bus)


def test_create_session_generates_expected_id(manager: SessionManager) -> None:
    session = manager.create_session("P023", Study.STUDY_1)
    assert session.session_id == "P023_S01"
    assert session.status == SessionStatus.CREATED
    assert session.study == Study.COMBINED_SESSION


def test_create_session_creates_folder_structure(manager: SessionManager, config: AppConfig) -> None:
    session = manager.create_session("P023", Study.STUDY_1)
    session_dir = Path(session.session_dir)

    assert session_dir == config.data_dir / "P023" / "S01"
    assert (session_dir / "session.json").is_file()
    assert (session_dir / "configuration.yaml").is_file()

    with open(session_dir / "session.json") as f:
        metadata = json.load(f)
    assert metadata["session_id"] == "P023_S01"
    assert metadata["participant_code"] == "P023"


def test_create_session_publishes_session_created_event(manager: SessionManager, event_bus: EventBus) -> None:
    received = []
    event_bus.event_published.connect(lambda e: received.append(e))

    session = manager.create_session("P023", Study.STUDY_1)

    assert len(received) == 1
    assert received[0].event_type == EventType.SESSION_CREATED
    assert received[0].session_id == session.session_id
    assert received[0].participant_id == "P023"


def test_start_and_end_session_updates_status(manager: SessionManager) -> None:
    session = manager.create_session("P023", Study.STUDY_1)

    manager.start_session(session)
    assert session.status == SessionStatus.IN_PROGRESS
    assert session.started_at is not None

    manager.end_session(session)
    assert session.status == SessionStatus.COMPLETED
    assert session.ended_at is not None

    reloaded = manager.get_session(session.session_id)
    assert reloaded is not None
    assert reloaded.status == SessionStatus.COMPLETED


def test_get_or_create_active_session_reuses_s01(manager: SessionManager) -> None:
    s1 = manager.get_or_create_active_session("P023")
    s1_again = manager.get_or_create_active_session("P023")
    assert s1.session_id == "P023_S01"
    assert s1_again.session_id == s1.session_id
    assert s1.status == SessionStatus.IN_PROGRESS


def test_second_session_for_same_participant_increments(manager: SessionManager) -> None:
    s1 = manager.create_session("P023", Study.STUDY_1)
    s2 = manager.create_session("P023", Study.STUDY_2)
    assert s1.session_id == "P023_S01"
    assert s2.session_id == "P023_S02"


def test_list_sessions_for_participant(manager: SessionManager) -> None:
    manager.create_session("P023", Study.STUDY_1)
    manager.create_session("P023", Study.STUDY_2)
    manager.create_session("P099", Study.STUDY_1)

    sessions = manager.list_sessions_for_participant("P023")
    assert {s.session_id for s in sessions} == {"P023_S01", "P023_S02"}


def test_workflow_steps_share_same_active_collection_session(
    manager: SessionManager, db: Database, event_bus: EventBus
) -> None:
    from core.workflow_manager import WorkflowManager
    from models.enums import WorkflowStep

    workflow = WorkflowManager(db, manager, event_bus)
    run1, s1 = workflow.start_run("P023", WorkflowStep.STUDY1_TRAINING)
    workflow.end_run(run1.run_id, completed=True)
    run2, s2 = workflow.start_run("P023", WorkflowStep.STUDY1_STUDY)

    assert s1.session_id == "P023_S01"
    assert s2.session_id == "P023_S01"
    assert Path(s2.session_dir).name == "S01"
