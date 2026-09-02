from __future__ import annotations

from pathlib import Path

from core.database import Database
from core.workflow_manager import WorkflowManager, condition_status_is_complete
from models.enums import StepOverallStatus, WorkflowStep


def _manager(tmp_path: Path) -> tuple[WorkflowManager, Database]:
    db = Database(tmp_path / "identifiable.sqlite3", tmp_path / "experimental.sqlite3")
    db.experimental_conn.execute(
        "INSERT INTO participants(participant_code, created_at) VALUES (?, ?)",
        ("P001", 0.0),
    )
    db.experimental_conn.commit()
    return WorkflowManager(db, session_manager=None, event_bus=None), db  # type: ignore[arg-type]


def test_condition_override_is_distinct_and_creates_no_fake_trial(tmp_path: Path) -> None:
    manager, db = _manager(tmp_path)
    manager.mark_completion_override(
        "P001",
        WorkflowStep.STUDY1_STUDY,
        item_key="grid_requested",
        reason="Equipment unavailable",
    )

    status = manager.study1_study_condition_status("P001", "grid_requested")
    assert status.status == "Manually Completed"
    assert condition_status_is_complete(status.status)
    assert manager.step_status("P001", WorkflowStep.STUDY1_STUDY).completed_count == 1
    assert db.experimental_conn.execute("SELECT COUNT(*) FROM trials").fetchone()[0] == 0


def test_whole_phase_override_completes_every_required_item(tmp_path: Path) -> None:
    manager, _db = _manager(tmp_path)
    manager.mark_completion_override(
        "P001",
        WorkflowStep.AGENT_OBSERVATION,
        reason="Participant ended the session early",
    )

    summary = manager.step_status("P001", WorkflowStep.AGENT_OBSERVATION)
    assert summary.completed_count == 2
    assert summary.overall_status == StepOverallStatus.COMPLETED
    assert all(
        item.status == "Manually Completed"
        for item in manager.observation_condition_statuses("P001")
    )
