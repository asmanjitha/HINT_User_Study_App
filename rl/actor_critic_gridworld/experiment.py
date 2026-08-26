"""GUI-independent Actor-Critic Gridworld experiment."""

from __future__ import annotations

import time

from pathlib import Path
from typing import Optional

from PySide6.QtCore import (
    QObject,
    QTimer,
    Signal,
)

from models.enums import (
    FeedbackTiming,
    Modality,
)

from models.trial import Trial

from rl.actor_critic_gridworld.ac_agent import (
    ActorCriticAgent,
)

from rl.actor_critic_gridworld.ambiguity_detector import (
    OscillationAmbiguityDetector,
)

from rl.actor_critic_gridworld.maze_env import (
    MazeEnv,
)


_ACTION_NAMES = [
    "UP",
    "DOWN",
    "LEFT",
    "RIGHT",
]


class ActorCriticGridworldExperiment(
    QObject
):

    view_updated = Signal(object)

    step_completed = Signal(object)

    episode_started = Signal(object)

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
        trial: Trial,
        config: dict,
        use_maze_qinit: bool = False,
        parent: QObject | None = None,
    ) -> None:

        super().__init__(parent)

        self.trial = trial
        self.config = config

        self.env = MazeEnv()

        actor_cfg = config.get(
            "actor",
            {},
        )

        human_cfg = config.get(
            "human_guidance",
            {},
        )

        seed = (
            trial.condition.random_seed
        )

        if seed is None:
            seed = config.get(
                "random_seed"
            )

        self.agent = ActorCriticAgent(
            state_size=(
                self.env.size,
                self.env.size,
            ),

            action_size=4,

            actor_lr=float(
                actor_cfg.get(
                    "actor_lr",
                    0.02,
                )
            ),

            critic_lr=float(
                actor_cfg.get(
                    "critic_lr",
                    0.10,
                )
            ),

            discount_factor=float(
                actor_cfg.get(
                    "discount_factor",
                    0.99,
                )
            ),

            entropy_coef=float(
                actor_cfg.get(
                    "entropy_coef",
                    0.05,
                )
            ),

            entropy_decay=float(
                actor_cfg.get(
                    "entropy_decay",
                    0.9995,
                )
            ),

            entropy_min=float(
                actor_cfg.get(
                    "entropy_min",
                    0.002,
                )
            ),

            logit_clip=float(
                actor_cfg.get(
                    "logit_clip",
                    10.0,
                )
            ),

            human_actor_boost=float(
                human_cfg.get(
                    "actor_boost",
                    3.0,
                )
            ),

            human_actor_reduce=float(
                human_cfg.get(
                    "actor_reduce",
                    1.0,
                )
            ),

            human_critic_bonus=float(
                human_cfg.get(
                    "critic_bonus",
                    5.0,
                )
            ),

            random_seed=seed,
        )

        if use_maze_qinit:

            self.agent.initialize_q_table_from_maze(
                self.env.maze,
                self.env.target_pos,
            )

        ambiguity_cfg = config.get(
            "ambiguity",
            {},
        )

        self.detector = (
            OscillationAmbiguityDetector(
                history_size=int(
                    ambiguity_cfg.get(
                        "history_size",
                        20,
                    )
                ),

                cooldown_steps=int(
                    ambiguity_cfg.get(
                        "prompt_cooldown_steps",
                        6,
                    )
                ),
            )
        )

        self.step_interval_ms = int(
            config.get(
                "step_interval_ms",
                50,
            )
        )

        self.feedback_timeout_seconds = int(
            config.get(
                "feedback_timeout_seconds",
                10,
            )
        )

        self.anytime_history_length = int(
            config.get(
                "anytime_history_length",
                10,
            )
        )

        self._step_timer = QTimer(self)

        self._step_timer.timeout.connect(
            self.step_once
        )

        self._feedback_timer = QTimer(
            self
        )

        self._feedback_timer.setSingleShot(
            True
        )

        self._feedback_timer.timeout.connect(
            self._on_feedback_timeout
        )

        self.current_episode = 1

        self.total_reward = 0.0

        self.human_feedback_given = False

        self.state = (
            self.env.reset_episode()
        )

        self.detector.reset(
            self.state
        )

        self._running = False

        self._paused_by_user = False

        self._waiting_for_feedback = False

        self._selecting_anytime_feedback = False

        self._anytime_history_snapshot = []

        self._anytime_pause_timestamp: Optional[
            float
        ] = None

        self._anytime_pause_step: Optional[
            int
        ] = None

        self._state_history = []

        self._pending_ambiguity: Optional[
            tuple[int, int]
        ] = None

        self._pending_request_timestamp: Optional[
            float
        ] = None

        self._last_action = None
        self._last_reward = None

        self._first_goal_saved = False

        if trial.trial_path is None:
            raise ValueError(
                "Trial has no storage directory"
            )

        self._snapshot_dir = (
            trial.trial_path
            / "rl"
            / "snapshots"
        )

        self._snapshot_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        self._reset_state_history()

    # ------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------

    def start(self) -> None:

        if self._running:
            return

        self._running = True
        self._paused_by_user = False

        self.status_changed.emit(
            "Running"
        )

        self.episode_started.emit(
            self._episode_identity_payload()
        )

        self._emit_view()

        self._step_timer.start(
            self.step_interval_ms
        )

    def pause(self) -> None:

        if not self._running:
            return

        self._paused_by_user = True

        self._step_timer.stop()
        self._feedback_timer.stop()

        self.status_changed.emit(
            "Paused"
        )

    def resume(self) -> None:

        if not self._running:
            return

        self._paused_by_user = False

        if self._selecting_anytime_feedback:

            self.status_changed.emit(
                "Selecting Feedback State"
            )

        elif self._waiting_for_feedback:

            self._feedback_timer.start(
                self.feedback_timeout_seconds
                * 1000
            )

            self.status_changed.emit(
                "Waiting for Feedback"
            )

        else:

            self._step_timer.start(
                self.step_interval_ms
            )

            self.status_changed.emit(
                "Running"
            )

    def stop(self) -> None:

        self._step_timer.stop()

        self._feedback_timer.stop()

        self._running = False

        self._paused_by_user = False

        self._waiting_for_feedback = False

        self._selecting_anytime_feedback = False
        self._anytime_history_snapshot = []
        self._anytime_pause_timestamp = None
        self._anytime_pause_step = None

        self.status_changed.emit(
            "Stopped"
        )

    # ------------------------------------------------------
    # Main RL step
    # ------------------------------------------------------

    def step_once(self) -> None:

        if (
            not self._running
            or self._paused_by_user
            or self._waiting_for_feedback
            or self._selecting_anytime_feedback
        ):
            return

        timestamp = time.time()

        state_before = tuple(
            self.state
        )

        full_state = self._full_state(
            state_before
        )

        action = self.agent.choose_action(
            full_state
        )

        (
            next_state,
            reward,
            done,
            target_reached,
        ) = self.env.step(action)

        state_after = tuple(
            next_state
        )

        next_full_state = self._full_state(
            state_after
        )

        self.agent.learn(
            full_state,
            action,
            reward,
            next_full_state,
            done,
        )

        self.state = state_after

        self._record_state_history(
            state_after
        )

        self.total_reward += reward

        self._last_action = action

        self._last_reward = float(
            reward
        )

        self.detector.add_position(
            state_after
        )

        ambiguous_pos = None

        if not done:
            ambiguous_pos = (
                self.detector.detect(
                    self.env.steps
                )
            )

        collision = bool(
            done
            and reward == -50
        )

        payload = {
            "timestamp":
                timestamp,

            "episode":
                self.current_episode,

            "step":
                self.env.steps,

            "state_before":
                state_before,

            "state_after":
                state_after,

            "action":
                action,

            "action_name":
                _ACTION_NAMES[action],

            "reward":
                float(reward),

            "total_reward":
                float(
                    self.total_reward
                ),

            "done":
                bool(done),

            "target_reached":
                bool(
                    target_reached
                ),

            "collision":
                collision,

            "entropy_coef":
                float(
                    self.agent.entropy_coef
                ),

            "ambiguity_detected":
                ambiguous_pos
                is not None,
        }

        self.step_completed.emit(
            payload
        )

        self._emit_view(
            ambiguous_position=
                ambiguous_pos
        )

        if target_reached:
            self._save_first_goal_snapshot()

        if ambiguous_pos is not None:

            self.detector.mark_detection(
                self.env.steps
            )

            critical_payload = {
                "timestamp":
                    time.time(),

                "episode":
                    self.current_episode,

                "step":
                    self.env.steps,

                "state":
                    tuple(
                        ambiguous_pos
                    ),

                "detection_method":
                    "oscillation_pattern",
            }

            self.critical_state_detected.emit(
                critical_payload
            )

            if (
                self.trial.condition
                .feedback_timing
                == FeedbackTiming.REQUESTED
            ):

                self._begin_feedback_request(
                    tuple(
                        ambiguous_pos
                    )
                )

                return

        if done:

            self._finish_episode(
                target_reached=
                    target_reached
            )

    # ------------------------------------------------------
    # Human feedback
    # ------------------------------------------------------

    def submit_human_feedback(
        self,
        action: Optional[int],
        modality: Modality,
        selected_step: Optional[int] = None,
    ) -> bool:

        if not self._running:
            return False

        if (
            action is not None
            and action not in
            (0, 1, 2, 3)
        ):
            raise ValueError(
                f"Invalid Gridworld "
                f"action: {action}"
            )

        # -------------------------------
        # Requested feedback
        # -------------------------------

        if (
            self.trial.condition
            .feedback_timing
            == FeedbackTiming.REQUESTED
        ):

            if not self._waiting_for_feedback:
                return False

            self._resolve_requested_feedback(
                action,
                modality,
                timeout=False,
            )

            return True

        # -------------------------------
        # Anytime feedback
        # -------------------------------

        if action is None:
            return False

        if not self._selecting_anytime_feedback:
            return False

        if selected_step is None:
            return False

        selected = next(
            (
                item
                for item in self._anytime_history_snapshot
                if item["step"] == selected_step
            ),
            None,
        )

        if selected is None:
            return False

        now = time.time()

        selected_state = tuple(
            selected["state"]
        )

        pause_step = (
            self._anytime_pause_step
            if self._anytime_pause_step is not None
            else self.env.steps
        )

        pause_ts = self._anytime_pause_timestamp

        self.agent.apply_human_guidance(
            state=self._full_state(
                selected_state
            ),

            chosen_action=action,
        )

        self.human_feedback_given = True

        latency_ms = None

        if pause_ts is not None:
            latency_ms = (
                now - pause_ts
            ) * 1000.0

        self.feedback_resolved.emit(
            {
                "episode":
                    self.current_episode,

                # step/state identify the historical state
                # that actually received the feedback.
                "step":
                    int(selected_step),

                "selected_step":
                    int(selected_step),

                "pause_step":
                    int(pause_step),

                "steps_back":
                    int(
                        pause_step
                        - selected_step
                    ),

                "state":
                    selected_state,

                "pause_state":
                    tuple(self.state),

                "requested":
                    False,

                "skipped":
                    False,

                "timeout":
                    False,

                "action":
                    action,

                "action_name":
                    _ACTION_NAMES[action],

                "modality":
                    modality.value,

                "request_timestamp":
                    None,

                "pause_timestamp":
                    pause_ts,

                "response_timestamp":
                    now,

                "response_latency_ms":
                    latency_ms,
            }
        )

        self._selecting_anytime_feedback = False
        self._anytime_history_snapshot = []
        self._anytime_pause_timestamp = None
        self._anytime_pause_step = None

        # Restore the live state in the participant view.
        self._emit_view()

        if (
            self._running
            and not self._paused_by_user
        ):

            self.status_changed.emit(
                "Running"
            )

            self._step_timer.start(
                self.step_interval_ms
            )

        return True

    def begin_anytime_feedback(
        self,
    ) -> bool:
        """Pause Anytime mode and expose recent visited states.

        This is a feedback-selection pause, not an administrative
        trial pause. The environment stays at the current live state.
        Guidance is later applied to the selected historical state.
        """

        if not self._running:
            return False

        if (
            self.trial.condition.feedback_timing
            != FeedbackTiming.ANYTIME
        ):
            return False

        if (
            self._paused_by_user
            or self._waiting_for_feedback
            or self._selecting_anytime_feedback
        ):
            return False

        self._step_timer.stop()

        self._selecting_anytime_feedback = True
        self._anytime_pause_timestamp = time.time()
        self._anytime_pause_step = int(
            self.env.steps
        )

        recent = self._state_history[
            -self.anytime_history_length:
        ]

        pause_step = int(self.env.steps)

        self._anytime_history_snapshot = [
            {
                "history_index": index + 1,
                "episode": int(item["episode"]),
                "step": int(item["step"]),
                "state": tuple(item["state"]),
                "timestamp": float(item["timestamp"]),
                "steps_back": int(
                    pause_step - item["step"]
                ),
            }
            for index, item in enumerate(recent)
        ]

        payload = {
            "episode":
                self.current_episode,

            "pause_step":
                pause_step,

            "pause_state":
                tuple(self.state),

            "pause_timestamp":
                self._anytime_pause_timestamp,

            "history":
                list(
                    self._anytime_history_snapshot
                ),
        }

        self.status_changed.emit(
            "Selecting Feedback State"
        )

        self.anytime_feedback_started.emit(
            payload
        )

        return True

    def _begin_feedback_request(
        self,
        position: tuple[int, int],
    ) -> None:

        self._waiting_for_feedback = True

        self._pending_ambiguity = (
            position
        )

        self._pending_request_timestamp = (
            time.time()
        )

        self._step_timer.stop()

        payload = {
            "episode":
                self.current_episode,

            "step":
                self.env.steps,

            "state":
                position,

            "request_timestamp":
                self._pending_request_timestamp,

            "timeout_seconds":
                self.feedback_timeout_seconds,
        }

        self.status_changed.emit(
            "Waiting for Feedback"
        )

        self.feedback_requested.emit(
            payload
        )

        self._feedback_timer.start(
            self.feedback_timeout_seconds
            * 1000
        )

    def _resolve_requested_feedback(
        self,
        action: Optional[int],
        modality: Optional[Modality],
        timeout: bool,
    ) -> None:

        if (
            not self._waiting_for_feedback
            or self._pending_ambiguity
            is None
        ):
            return

        self._feedback_timer.stop()

        response_ts = time.time()

        request_ts = (
            self._pending_request_timestamp
        )

        state = (
            self._pending_ambiguity
        )

        if action is not None:

            self.agent.apply_human_guidance(
                state=self._full_state(
                    state
                ),

                chosen_action=action,
            )

            self.human_feedback_given = True

        latency_ms = None

        if request_ts is not None:

            latency_ms = (
                response_ts
                - request_ts
            ) * 1000.0

        self.feedback_resolved.emit(
            {
                "episode":
                    self.current_episode,

                "step":
                    self.env.steps,

                "state":
                    state,

                "requested":
                    True,

                "skipped":
                    action is None,

                "timeout":
                    timeout,

                "action":
                    action,

                "action_name":
                    (
                        ""
                        if action is None
                        else _ACTION_NAMES[
                            action
                        ]
                    ),

                "modality":
                    (
                        ""
                        if modality is None
                        else modality.value
                    ),

                "request_timestamp":
                    request_ts,

                "response_timestamp":
                    response_ts,

                "response_latency_ms":
                    latency_ms,
            }
        )

        self._waiting_for_feedback = False

        self._pending_ambiguity = None

        self._pending_request_timestamp = None

        self._emit_view()

        if (
            self._running
            and not self._paused_by_user
        ):

            self.status_changed.emit(
                "Running"
            )

            self._step_timer.start(
                self.step_interval_ms
            )

    def _on_feedback_timeout(
        self,
    ) -> None:

        self._resolve_requested_feedback(
            action=None,
            modality=None,
            timeout=True,
        )

    # ------------------------------------------------------
    # Episode handling
    # ------------------------------------------------------

    def _finish_episode(
        self,
        target_reached: bool,
    ) -> None:

        payload = {
            "timestamp":
                time.time(),

            "episode":
                self.current_episode,

            "steps":
                self.env.steps,

            "total_reward":
                float(
                    self.total_reward
                ),

            "target_reached":
                bool(
                    target_reached
                ),

            "human_feedback_given":
                bool(
                    self.human_feedback_given
                ),
        }

        self.episode_finished.emit(
            payload
        )

        self._maybe_save_periodic_snapshot(
            self.current_episode
        )

        self.current_episode += 1

        self.total_reward = 0.0

        self.human_feedback_given = False

        self._last_action = None

        self._last_reward = None

        self.state = (
            self.env.reset_episode()
        )

        self.detector.reset(
            self.state
        )

        self._reset_state_history()

        self.episode_started.emit(
            self._episode_identity_payload()
        )

        self._emit_view()

    # ------------------------------------------------------
    # Snapshots
    # ------------------------------------------------------

    def _save_first_goal_snapshot(
        self,
    ) -> None:

        if self._first_goal_saved:
            return

        self.agent.save_q_table(
            self._snapshot_dir
            / "ac_tables_first_goal.txt"
        )

        self._first_goal_saved = True

    def _maybe_save_periodic_snapshot(
        self,
        episode_number: int,
    ) -> None:

        if episode_number in (
            99,
            101,
            249,
            251,
            499,
            501,
            999,
            1001,
        ):

            self.agent.save_q_table(
                self._snapshot_dir
                / (
                    f"ac_tables_ep"
                    f"{episode_number}.txt"
                )
            )

    # ------------------------------------------------------
    # Helpers
    # ------------------------------------------------------

    def _reset_state_history(
        self,
    ) -> None:

        self._state_history = []

        self._record_state_history(
            tuple(self.state)
        )

    def _record_state_history(
        self,
        state: tuple[int, int],
    ) -> None:

        self._state_history.append(
            {
                "episode":
                    self.current_episode,

                "step":
                    int(self.env.steps),

                "state":
                    tuple(state),

                "timestamp":
                    time.time(),
            }
        )

        max_keep = max(
            self.anytime_history_length,
            1,
        )

        if len(self._state_history) > max_keep:
            self._state_history = (
                self._state_history[-max_keep:]
            )

    def _full_state(
        self,
        state: tuple[int, int],
    ) -> tuple[
        int,
        int,
        int,
        int,
    ]:

        return (
            state[0],
            state[1],

            self.env.target_pos[0],
            self.env.target_pos[1],
        )

    def _episode_identity_payload(
        self,
    ) -> dict:

        return {
            "episode":
                self.current_episode,

            "step":
                self.env.steps,

            "timestamp":
                time.time(),
        }

    def _emit_view(
        self,
        ambiguous_position=None,
    ) -> None:

        self.view_updated.emit(
            {
                "maze":
                    self.env.maze.copy(),

                "agent_position":
                    tuple(
                        self.env.agent_pos
                    ),

                "target_position":
                    tuple(
                        self.env.target_pos
                    ),

                "ambiguous_position":
                    ambiguous_position,

                "episode":
                    self.current_episode,

                "step":
                    self.env.steps,

                "total_reward":
                    float(
                        self.total_reward
                    ),

                "entropy_coef":
                    float(
                        self.agent.entropy_coef
                    ),

                "last_action":
                    self._last_action,

                "last_action_name":
                    (
                        ""
                        if self._last_action
                        is None
                        else _ACTION_NAMES[
                            self._last_action
                        ]
                    ),

                "last_reward":
                    self._last_reward,
            }
        )