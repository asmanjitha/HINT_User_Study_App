from __future__ import annotations

import json
import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from core.application_controller import ApplicationController
from core.observation_video_settings import (
    load_observation_video_paths,
    resolve_observation_video_path,
    save_observation_video_paths,
)
from models.enums import (
    CollectionRunStatus,
    Environment,
    EventType,
    FeedbackTiming,
    Modality,
    Study,
)
from models.trial import ExperimentCondition, Trial


def test_participant_start_gate_hides_native_video_surface_until_start(tmp_path: Path) -> None:
    """The stopped QVideoWidget must not cover the Start gate on Windows."""
    del tmp_path  # Source-level test stays runnable on CI hosts without libEGL.
    source = (Path(__file__).parents[1] / "gui" / "observation_video_window.py").read_text(
        encoding="utf-8"
    )
    module = ast.parse(source)
    prepare = next(
        node
        for node in ast.walk(module)
        if isinstance(node, ast.FunctionDef) and node.name == "_prepare"
    )
    body = ast.unparse(prepare)

    assert "self._video_widget.hide()" in body
    assert body.index("self._video_widget.hide()") < body.index(
        "self._start_overlay.present(trial)"
    )


def test_video_surface_is_shown_only_after_participant_start_is_accepted() -> None:
    source = (Path(__file__).parents[1] / "gui" / "observation_video_window.py").read_text(
        encoding="utf-8"
    )
    module = ast.parse(source)
    start = next(
        node
        for node in ast.walk(module)
        if isinstance(node, ast.FunctionDef)
        and node.name == "_participant_start_requested"
    )
    body = ast.unparse(start)

    activity_started = body.index("self._controller.start_prepared_activity(trial_id)")
    video_shown = body.index("self._video_widget.show()")
    playback_started = body.index("self._player.play()")
    assert activity_started < video_shown < playback_started


def test_video_path_settings_round_trip(tmp_path: Path) -> None:
    saved = {"gridworld": r"D:\videos\grid.mp4", "continuous_room": "videos/room.mp4"}
    save_observation_video_paths(tmp_path, saved)
    assert load_observation_video_paths(tmp_path) == saved
    assert resolve_observation_video_path("videos/room.mp4", tmp_path) == (
        tmp_path / "videos" / "room.mp4"
    ).resolve()


def test_prepare_observation_video_writes_source_and_prepares_start_gate(tmp_path: Path) -> None:
    video = tmp_path / "grid.mp4"
    video.write_bytes(b"test video placeholder")
    trial_dir = tmp_path / "trial"
    trial_dir.mkdir()
    condition = ExperimentCondition(
        study=Study.OBSERVATION,
        environment=Environment.GRIDWORLD,
        feedback_timing=FeedbackTiming.NOT_APPLICABLE,
        modality=Modality.NONE,
        rl_algorithm="prerecorded_gridworld_training_video",
    )
    trial = Trial(
        trial_id="P001_S01_OBS_T01_R01",
        session_id="P001_S01",
        participant_code="P001",
        condition=condition,
        trial_dir=str(trial_dir),
    )
    session = SimpleNamespace(study=Study.COMBINED_SESSION)

    controller = ApplicationController.__new__(ApplicationController)
    controller.active_trial = None
    controller.active_session = None
    controller._active_trial_backend = "none"
    controller._activity_started = False
    controller._observation_video_path = None
    controller.session_manager = Mock()
    controller.session_manager.get_session.return_value = session
    controller.trial_manager = Mock()
    controller.trial_manager.create_trial.return_value = trial
    controller.event_bus = Mock()

    result = controller.prepare_observation_video_trial(
        "P001_S01", condition, video
    )

    assert result is trial
    assert controller._active_trial_backend == "observation_video"
    assert controller.observation_video_path_for_trial(trial.trial_id) == video.resolve()
    metadata = json.loads(
        (trial_dir / "observation_video" / "source.json").read_text(encoding="utf-8")
    )
    assert metadata["file_name"] == "grid.mp4"
    assert metadata["playback_mode"] == "fullscreen"
    assert metadata["participant_start_required"] is True
    event = controller.event_bus.publish.call_args.args[0]
    assert event.event_type == EventType.ACTIVITY_PREPARED
    assert event.trial_id == trial.trial_id


def test_natural_video_end_marks_trial_and_workflow_valid() -> None:
    condition = SimpleNamespace(study=Study.OBSERVATION)
    trial = SimpleNamespace(
        trial_id="P001_S01_OBS_T01_R01",
        participant_code="P001",
        condition=condition,
    )
    run = SimpleNamespace(run_id="P001_AGENT_OBSERVATION_01", trial_id=trial.trial_id)
    controller = ApplicationController.__new__(ApplicationController)
    controller.active_trial = trial
    controller._active_trial_backend = "observation_video"
    controller._activity_started = True
    controller.workflow_manager = Mock()
    controller.workflow_manager.has_active_run.return_value = run
    controller.stop_active_trial = Mock()

    assert controller.complete_active_observation_video(trial.trial_id) is True
    controller.stop_active_trial.assert_called_once_with(
        completed=True, collection_status=CollectionRunStatus.VALID
    )
    controller.workflow_manager.end_run.assert_called_once_with(
        run.run_id,
        completed=True,
        notes="Automatically completed when the observation video ended.",
        outcome=CollectionRunStatus.VALID,
    )
