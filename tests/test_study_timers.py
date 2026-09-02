from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

from core.application_controller import ApplicationController
from models.enums import CollectionRunStatus, EventType


def _timed_controller(*, matching_run: bool = True) -> tuple[ApplicationController, Mock, Mock]:
    controller = ApplicationController.__new__(ApplicationController)
    controller.active_trial = SimpleNamespace(
        trial_id="P001_S01_ST1_T01_R01",
        participant_code="P001",
        session_id="P001_S01",
        practice=False,
        readable_run_label="T01 / R01",
    )
    run = SimpleNamespace(
        run_id="P001_S1S_01",
        trial_id=("P001_S01_ST1_T01_R01" if matching_run else "another-trial"),
    )
    workflow = Mock()
    workflow.has_active_run.return_value = run
    events = Mock()
    controller.workflow_manager = workflow
    controller.event_bus = events

    def stop_trial(**_kwargs) -> None:
        controller.active_trial = None

    controller.stop_active_trial = Mock(side_effect=stop_trial)
    return controller, workflow, events


def test_time_limit_marks_trial_and_workflow_valid() -> None:
    controller, workflow, events = _timed_controller()

    completed = controller.complete_active_trial_at_time_limit(
        "P001_S01_ST1_T01_R01", 480
    )

    assert completed is True
    controller.stop_active_trial.assert_called_once_with(
        completed=True,
        collection_status=CollectionRunStatus.VALID,
    )
    workflow.end_run.assert_called_once_with(
        "P001_S1S_01",
        completed=True,
        notes="Automatically completed at the 8-minute protocol time limit.",
        outcome=CollectionRunStatus.VALID,
    )
    event = events.publish.call_args.args[0]
    assert event.event_type == EventType.TRIAL_TIME_LIMIT_REACHED
    assert event.trial_id == "P001_S01_ST1_T01_R01"


def test_time_limit_refuses_to_close_unmatched_workflow_run() -> None:
    controller, workflow, events = _timed_controller(matching_run=False)

    completed = controller.complete_active_trial_at_time_limit(
        "P001_S01_ST1_T01_R01", 480
    )

    assert completed is False
    controller.stop_active_trial.assert_not_called()
    workflow.end_run.assert_not_called()
    events.publish.assert_not_called()
