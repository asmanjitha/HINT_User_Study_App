from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core.application_controller import ApplicationController
from models.enums import EventType, SessionStatus


def _prepared_controller(backend: str = "local_gridworld"):
    controller = ApplicationController.__new__(ApplicationController)
    trial = SimpleNamespace(
        trial_id="P001_S01_ST1_T01_R01",
        participant_code="P001",
        session_id="P001_S01",
        condition_code="T01",
        run_code="R01",
    )
    session = SimpleNamespace(status=SessionStatus.CREATED)
    controller.active_trial = trial
    controller.active_session = session
    controller._active_trial_backend = backend
    controller._activity_started = False
    controller.session_manager = Mock()
    controller.trial_manager = Mock()
    controller.rl_manager = Mock()
    controller.continuous_nav_client = Mock()
    controller.event_bus = Mock()
    controller._start_trial_sensor_recordings = Mock()
    return controller, trial, session


def test_participant_click_starts_lifecycle_sensors_and_local_backend() -> None:
    controller, trial, session = _prepared_controller()

    result = controller.start_prepared_activity(trial.trial_id)

    assert result is trial
    controller.session_manager.start_session.assert_called_once_with(session)
    controller.trial_manager.start_trial.assert_called_once_with(trial)
    controller._start_trial_sensor_recordings.assert_called_once_with(trial)
    controller.rl_manager.start.assert_called_once_with()
    assert controller.activity_started is True
    event = controller.event_bus.publish.call_args.args[0]
    assert event.event_type == EventType.PARTICIPANT_ACTIVITY_STARTED
    assert event.trial_id == trial.trial_id


def test_participant_click_starts_remote_backend_only_after_preparation() -> None:
    controller, trial, _session = _prepared_controller("remote_continuous_room")

    controller.start_prepared_activity(trial.trial_id)

    controller.continuous_nav_client.start_trial.assert_called_once_with()
    controller.rl_manager.start.assert_not_called()


def test_stale_participant_start_button_cannot_start_another_trial() -> None:
    controller, _trial, _session = _prepared_controller()

    with pytest.raises(RuntimeError, match="no longer waiting"):
        controller.start_prepared_activity("different-trial")

    controller.trial_manager.start_trial.assert_not_called()
    controller._start_trial_sensor_recordings.assert_not_called()
