"""RL manager."""

from __future__ import annotations

import json

from typing import Optional

from PySide6.QtCore import (
    QObject,
    Signal,
)

from core.config_loader import AppConfig
from core.event_bus import EventBus

from models.enums import (
    EventType,
    Modality,
)

from models.event import StudyEvent
from models.trial import Trial

from recording.rl_trial_recorder import (
    RLTrialRecorder,
)

from rl.actor_critic_gridworld.experiment import (
    ActorCriticGridworldExperiment,
)


class RLManager(QObject):

    trial_started = Signal(object)

    state_updated = Signal(object)

    episode_finished = Signal(object)

    critical_state_detected = Signal(
        object
    )

    feedback_requested = Signal(object)

    feedback_resolved = Signal(object)

    anytime_feedback_started = Signal(object)

    status_changed = Signal(str)

    def __init__(
        self,
        config: AppConfig,
        event_bus: EventBus,
        parent: QObject | None = None,
    ) -> None:

        super().__init__(parent)

        self._config = config

        self._event_bus = event_bus

        self._experiment = None

        self._recorder = None

        self.current_trial = None

    def prepare_actor_critic_trial(
        self,
        trial: Trial,
        use_maze_qinit: bool = False,
    ) -> None:

        if self._experiment is not None:
            raise RuntimeError(
                "An RL trial is already "
                "prepared/running"
            )

        if trial.trial_path is None:
            raise ValueError(
                "Trial has no data directory"
            )

        rl_cfg = (
            self._config.study_raw
            .get("rl", {})
            .get(
                "actor_critic_gridworld",
                {},
            )
            .copy()
        )

        if "random_seed" not in rl_cfg:

            rl_cfg["random_seed"] = (
                self._config.study_raw.get(
                    "random_seed",
                    42,
                )
            )

        with (
            trial.trial_path
            / "rl"
            / "rl_config.json"
        ).open(
            "w",
            encoding="utf-8",
        ) as f:

            json.dump(
                {
                    "algorithm":
                        "actor_critic_gridworld",

                    "use_maze_qinit":
                        use_maze_qinit,

                    "config":
                        rl_cfg,

                    "trial_condition":
                        trial.condition.to_dict(),
                },
                f,
                indent=2,
            )

        self.current_trial = trial

        self._recorder = RLTrialRecorder(
            trial,
            self._event_bus,
        )

        self._experiment = (
            ActorCriticGridworldExperiment(
                trial=trial,
                config=rl_cfg,
                use_maze_qinit=
                    use_maze_qinit,
            )
        )

        self._experiment.view_updated.connect(
            self.state_updated.emit
        )

        self._experiment.step_completed.connect(
            self._on_step_completed
        )

        self._experiment.episode_started.connect(
            self._on_episode_started
        )

        self._experiment.episode_finished.connect(
            self._on_episode_finished
        )

        self._experiment.critical_state_detected.connect(
            self._on_critical_state
        )

        self._experiment.feedback_requested.connect(
            self._on_feedback_requested
        )

        self._experiment.feedback_resolved.connect(
            self._on_feedback_resolved
        )

        self._experiment.anytime_feedback_started.connect(
            self._on_anytime_feedback_started
        )

        self._experiment.status_changed.connect(
            self.status_changed.emit
        )

    def start(self) -> None:

        if (
            self._experiment is None
            or self.current_trial is None
        ):
            raise RuntimeError(
                "No RL trial has been prepared"
            )

        self._experiment.start()

        self.trial_started.emit(
            self.current_trial
        )

    def pause(self) -> None:

        if self._experiment is not None:
            self._experiment.pause()

    def resume(self) -> None:

        if self._experiment is not None:
            self._experiment.resume()

    def stop(self) -> None:

        if self._experiment is not None:
            self._experiment.stop()

    def begin_anytime_feedback(
        self,
    ) -> bool:

        if self._experiment is None:
            return False

        return (
            self._experiment
            .begin_anytime_feedback()
        )

    def submit_feedback(
        self,
        action: Optional[int],
        modality: Modality,
        selected_step: Optional[int] = None,
    ) -> bool:

        if self._experiment is None:
            return False

        return (
            self._experiment
            .submit_human_feedback(
                action,
                modality,
                selected_step=selected_step,
            )
        )

    def finalize_trial(self) -> None:

        if self._experiment is not None:

            self._experiment.stop()

            self._experiment.deleteLater()

        self._experiment = None

        if self._recorder is not None:

            self._recorder.close()

        self._recorder = None

        self.current_trial = None

    # --------------------------------------------------

    def _on_step_completed(
        self,
        payload: dict,
    ) -> None:

        if self._recorder is not None:

            self._recorder.record_step(
                payload
            )

        trial = self.current_trial

        if (
            trial is not None
            and payload.get(
                "collision"
            )
        ):

            self._event_bus.publish(
                StudyEvent(
                    event_type=
                        EventType.COLLISION,

                    participant_id=
                        trial.participant_code,

                    session_id=
                        trial.session_id,

                    trial_id=
                        trial.trial_id,

                    episode=
                        payload["episode"],

                    step=
                        payload["step"],

                    value=str(
                        payload[
                            "state_after"
                        ]
                    ),
                )
            )

    def _on_episode_started(
        self,
        payload: dict,
    ) -> None:

        trial = self.current_trial

        if trial is None:
            return

        self._event_bus.publish(
            StudyEvent(
                event_type=
                    EventType.EPISODE_STARTED,

                participant_id=
                    trial.participant_code,

                session_id=
                    trial.session_id,

                trial_id=
                    trial.trial_id,

                episode=
                    payload["episode"],

                step=
                    payload["step"],
            )
        )

    def _on_episode_finished(
        self,
        payload: dict,
    ) -> None:

        trial = self.current_trial

        if trial is None:
            return

        if self._recorder is not None:

            self._recorder.record_episode(
                payload
            )

        self._event_bus.publish(
            StudyEvent(
                event_type=
                    EventType.EPISODE_ENDED,

                participant_id=
                    trial.participant_code,

                session_id=
                    trial.session_id,

                trial_id=
                    trial.trial_id,

                episode=
                    payload["episode"],

                step=
                    payload["steps"],

                value=(
                    f"reward="
                    f"{payload['total_reward']}"
                ),
            )
        )

        if payload["target_reached"]:

            self._event_bus.publish(
                StudyEvent(
                    event_type=
                        EventType.GOAL_REACHED,

                    participant_id=
                        trial.participant_code,

                    session_id=
                        trial.session_id,

                    trial_id=
                        trial.trial_id,

                    episode=
                        payload["episode"],

                    step=
                        payload["steps"],
                )
            )

        self.episode_finished.emit(
            payload
        )

    def _on_critical_state(
        self,
        payload: dict,
    ) -> None:

        trial = self.current_trial

        if trial is None:
            return

        self._event_bus.publish(
            StudyEvent(
                event_type=
                    EventType.CRITICAL_STATE,

                participant_id=
                    trial.participant_code,

                session_id=
                    trial.session_id,

                trial_id=
                    trial.trial_id,

                episode=
                    payload["episode"],

                step=
                    payload["step"],

                value=(
                    f"state="
                    f"{payload['state']};"
                    f"method="
                    f"{payload['detection_method']}"
                ),
            )
        )

        self.critical_state_detected.emit(
            payload
        )

    def _on_feedback_requested(
        self,
        payload: dict,
    ) -> None:

        trial = self.current_trial

        if trial is None:
            return

        self._event_bus.publish(
            StudyEvent(
                event_type=
                    EventType.FEEDBACK_REQUESTED,

                participant_id=
                    trial.participant_code,

                session_id=
                    trial.session_id,

                trial_id=
                    trial.trial_id,

                episode=
                    payload["episode"],

                step=
                    payload["step"],

                value=str(
                    payload["state"]
                ),
            )
        )

        self.feedback_requested.emit(
            payload
        )

    def _on_anytime_feedback_started(
        self,
        payload: dict,
    ) -> None:

        trial = self.current_trial

        if trial is None:
            return

        history_steps = [
            str(item.get("step", ""))
            for item in payload.get(
                "history",
                [],
            )
        ]

        self._event_bus.publish(
            StudyEvent(
                event_type=
                    EventType.ANYTIME_FEEDBACK_STARTED,

                participant_id=
                    trial.participant_code,

                session_id=
                    trial.session_id,

                trial_id=
                    trial.trial_id,

                episode=
                    payload["episode"],

                step=
                    payload["pause_step"],

                value=(
                    f"pause_state={payload['pause_state']};"
                    f"history_steps={','.join(history_steps)}"
                ),
            )
        )

        self.anytime_feedback_started.emit(
            payload
        )

    def _on_feedback_resolved(
        self,
        payload: dict,
    ) -> None:

        trial = self.current_trial

        if trial is None:
            return

        if self._recorder is not None:

            self._recorder.record_intervention(
                payload
            )

        if payload["skipped"]:

            self._event_bus.publish(
                StudyEvent(
                    event_type=
                        EventType.FEEDBACK_SKIPPED,

                    participant_id=
                        trial.participant_code,

                    session_id=
                        trial.session_id,

                    trial_id=
                        trial.trial_id,

                    episode=
                        payload["episode"],

                    step=
                        payload["step"],

                    value=(
                        "timeout"
                        if payload["timeout"]
                        else "manual_skip"
                    ),
                )
            )

        else:

            value = (
                f"{payload.get('modality', '')}:"
                f"{payload.get('action_name', '')}"
            )

            self._event_bus.publish(
                StudyEvent(
                    event_type=
                        EventType.FEEDBACK_RECEIVED,

                    participant_id=
                        trial.participant_code,

                    session_id=
                        trial.session_id,

                    trial_id=
                        trial.trial_id,

                    episode=
                        payload["episode"],

                    step=
                        payload["step"],

                    value=value,
                )
            )

            self._event_bus.publish(
                StudyEvent(
                    event_type=
                        EventType.FEEDBACK_APPLIED,

                    participant_id=
                        trial.participant_code,

                    session_id=
                        trial.session_id,

                    trial_id=
                        trial.trial_id,

                    episode=
                        payload["episode"],

                    step=
                        payload["step"],

                    value=value,
                )
            )

        self.feedback_resolved.emit(
            payload
        )