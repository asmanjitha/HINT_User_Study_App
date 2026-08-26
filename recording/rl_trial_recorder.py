"""CSV recorder for a single RL trial."""

from __future__ import annotations

import csv
from pathlib import Path

from core.event_bus import EventBus
from models.event import StudyEvent
from models.trial import Trial


class RLTrialRecorder:

    def __init__(
        self,
        trial: Trial,
        event_bus: EventBus,
    ) -> None:

        if trial.trial_path is None:
            raise ValueError(
                "Trial has no storage directory"
            )

        self.trial = trial
        self._event_bus = event_bus

        root = trial.trial_path

        (
            self._step_file,
            self._step_writer,
        ) = self._open_writer(
            root / "rl" / "rl_steps.csv",
            [
                "timestamp",

                "participant_id",
                "session_id",
                "trial_id",
                "condition_code",
                "run_code",
                "condition_name",

                "practice",

                "study",
                "environment",
                "feedback_timing",
                "modality",

                "episode",
                "step",

                "state_before_row",
                "state_before_col",

                "action",
                "action_name",

                "state_after_row",
                "state_after_col",

                "reward",
                "total_reward",

                "done",
                "target_reached",
                "collision",

                "entropy_coef",
                "ambiguity_detected",
            ],
        )

        (
            self._episode_file,
            self._episode_writer,
        ) = self._open_writer(
            root / "rl" / "rl_episodes.csv",
            [
                "timestamp",

                "participant_id",
                "session_id",
                "trial_id",
                "condition_code",
                "run_code",
                "condition_name",

                "practice",

                "episode",
                "steps",
                "total_reward",

                "target_reached",
                "human_feedback_given",
            ],
        )

        (
            self._intervention_file,
            self._intervention_writer,
        ) = self._open_writer(
            root / "rl" / "interventions.csv",
            [
                "participant_id",
                "session_id",
                "trial_id",
                "condition_code",
                "run_code",
                "condition_name",

                "practice",

                "episode",
                "step",
                "selected_step",
                "pause_step",
                "steps_back",

                "feedback_timing",
                "modality",

                "requested",
                "skipped",
                "timeout",

                "state_row",
                "state_col",

                "action",
                "action_name",

                "request_timestamp",
                "pause_timestamp",
                "response_timestamp",
                "response_latency_ms",
            ],
        )

        (
            self._event_file,
            self._event_writer,
        ) = self._open_writer(
            root / "events" / "events.csv",
            [
                "timestamp",
                "event",
                "participant_id",
                "session_id",
                "trial_id",
                "episode",
                "step",
                "value",
            ],
        )

        self._closed = False

        self._event_bus.event_published.connect(
            self._on_event
        )

    @staticmethod
    def _open_writer(
        path: Path,
        fieldnames: list[str],
    ):

        path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        f = path.open(
            "w",
            newline="",
            encoding="utf-8",
        )

        writer = csv.DictWriter(
            f,
            fieldnames=fieldnames,
        )

        writer.writeheader()
        f.flush()

        return f, writer

    def _common(self) -> dict:

        condition = self.trial.condition

        return {
            "participant_id":
                self.trial.participant_code,

            "session_id":
                self.trial.session_id,

            "trial_id":
                self.trial.trial_id,

            "condition_code":
                self.trial.condition_code,

            "run_code":
                self.trial.run_code,

            "condition_name":
                self.trial.condition_name,

            "practice":
                int(self.trial.practice),

            "study":
                condition.study.value,

            "environment":
                condition.environment.value,

            "feedback_timing":
                condition.feedback_timing.value,

            "modality":
                condition.modality.value,
        }

    def record_step(
        self,
        payload: dict,
    ) -> None:

        before = payload["state_before"]
        after = payload["state_after"]

        row = self._common()

        row.update(
            {
                "timestamp":
                    f"{payload['timestamp']:.6f}",

                "episode":
                    payload["episode"],

                "step":
                    payload["step"],

                "state_before_row":
                    before[0],

                "state_before_col":
                    before[1],

                "action":
                    payload["action"],

                "action_name":
                    payload["action_name"],

                "state_after_row":
                    after[0],

                "state_after_col":
                    after[1],

                "reward":
                    payload["reward"],

                "total_reward":
                    payload["total_reward"],

                "done":
                    int(payload["done"]),

                "target_reached":
                    int(
                        payload[
                            "target_reached"
                        ]
                    ),

                "collision":
                    int(
                        payload["collision"]
                    ),

                "entropy_coef":
                    payload["entropy_coef"],

                "ambiguity_detected":
                    int(
                        payload[
                            "ambiguity_detected"
                        ]
                    ),
            }
        )

        self._step_writer.writerow(row)
        self._step_file.flush()

    def record_episode(
        self,
        payload: dict,
    ) -> None:

        row = {
            "timestamp":
                f"{payload['timestamp']:.6f}",

            "participant_id":
                self.trial.participant_code,

            "session_id":
                self.trial.session_id,

            "trial_id":
                self.trial.trial_id,

            "condition_code":
                self.trial.condition_code,

            "run_code":
                self.trial.run_code,

            "condition_name":
                self.trial.condition_name,

            "practice":
                int(self.trial.practice),

            "episode":
                payload["episode"],

            "steps":
                payload["steps"],

            "total_reward":
                payload["total_reward"],

            "target_reached":
                int(
                    payload[
                        "target_reached"
                    ]
                ),

            "human_feedback_given":
                int(
                    payload[
                        "human_feedback_given"
                    ]
                ),
        }

        self._episode_writer.writerow(row)
        self._episode_file.flush()

    def record_intervention(
        self,
        payload: dict,
    ) -> None:

        latency = payload.get(
            "response_latency_ms"
        )

        row = {
            "participant_id":
                self.trial.participant_code,

            "session_id":
                self.trial.session_id,

            "trial_id":
                self.trial.trial_id,

            "condition_code":
                self.trial.condition_code,

            "run_code":
                self.trial.run_code,

            "condition_name":
                self.trial.condition_name,

            "practice":
                int(self.trial.practice),

            "episode":
                payload["episode"],

            "step":
                payload["step"],

            "selected_step":
                payload.get(
                    "selected_step",
                    payload["step"],
                ),

            "pause_step":
                (
                    ""
                    if payload.get("pause_step") is None
                    else payload["pause_step"]
                ),

            "steps_back":
                (
                    ""
                    if payload.get("steps_back") is None
                    else payload["steps_back"]
                ),

            "feedback_timing":
                self.trial.condition
                .feedback_timing.value,

            "modality":
                payload.get(
                    "modality",
                    "",
                ),

            "requested":
                int(
                    payload["requested"]
                ),

            "skipped":
                int(
                    payload["skipped"]
                ),

            "timeout":
                int(
                    payload["timeout"]
                ),

            "state_row":
                payload["state"][0],

            "state_col":
                payload["state"][1],

            "action":
                (
                    ""
                    if payload.get("action")
                    is None
                    else payload["action"]
                ),

            "action_name":
                payload.get(
                    "action_name",
                    "",
                ),

            "request_timestamp":
                (
                    ""
                    if payload.get(
                        "request_timestamp"
                    ) is None
                    else
                    f"{payload['request_timestamp']:.6f}"
                ),

            "pause_timestamp":
                (
                    ""
                    if payload.get(
                        "pause_timestamp"
                    ) is None
                    else
                    f"{payload['pause_timestamp']:.6f}"
                ),

            "response_timestamp":
                f"{payload['response_timestamp']:.6f}",

            "response_latency_ms":
                (
                    ""
                    if latency is None
                    else f"{latency:.3f}"
                ),
        }

        self._intervention_writer.writerow(
            row
        )

        self._intervention_file.flush()

    def _on_event(
        self,
        event: StudyEvent,
    ) -> None:

        if (
            event.trial_id
            != self.trial.trial_id
        ):
            return

        self._event_writer.writerow(
            event.to_csv_row()
        )

        self._event_file.flush()

    def close(self) -> None:

        if self._closed:
            return

        self._closed = True

        try:
            self._event_bus.event_published.disconnect(
                self._on_event
            )
        except (RuntimeError, TypeError):
            pass

        for file in (
            self._step_file,
            self._episode_file,
            self._intervention_file,
            self._event_file,
        ):
            file.flush()
            file.close()