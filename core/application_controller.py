"""Application controller."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from core.config_loader import (
    AppConfig,
    load_config,
)

from core.database import Database
from core.event_bus import EventBus

from core.logging_setup import (
    setup_logging,
)

from core.participant_manager import (
    ParticipantManager,
)

from core.session_manager import (
    SessionManager,
)

from core.sensor_policy import eye_tracker_for_trial

from core.trial_manager import (
    TrialManager,
)

from core.workflow_manager import (
    WorkflowManager,
)

from devices.device_manager import (
    DeviceManager,
)

from models.enums import (
    CollectionRunStatus,
    EventType,
    SessionStatus,
    Study,
    DeviceType,
)

from models.event import StudyEvent

from models.session import Session

from models.trial import (
    ExperimentCondition,
    Trial,
)

from rl.rl_manager import RLManager
from remote.continuous_nav_client import ContinuousNavClient


logger = logging.getLogger(
    __name__
)


class ApplicationController:

    def __init__(self) -> None:

        self.config: AppConfig = (
            load_config()
        )

        setup_logging(
            self.config.logs_dir,
            self.config.config_dir
            / "logging.yaml",
        )

        logger.info(
            "Starting HINT Study Console "
            "v%s (mode=%s)",

            self.config.raw
            .get("app", {})
            .get(
                "version",
                "unknown",
            ),

            self.config.mode.value,
        )

        self.event_bus = EventBus()

        self.db = Database(
            self.config.identifiable_db,
            self.config.experimental_db,
        )

        self.participant_manager = (
            ParticipantManager(
                self.db
            )
        )

        self.session_manager = (
            SessionManager(
                self.db,
                self.config,
                self.event_bus,
            )
        )

        self.trial_manager = (
            TrialManager(
                self.db,
                self.event_bus,
            )
        )

        self.rl_manager = RLManager(
            self.config,
            self.event_bus,
        )

        self.workflow_manager = (
            WorkflowManager(
                self.db,
                self.session_manager,
                self.event_bus,
            )
        )

        self.device_manager = (
            DeviceManager(
                self.event_bus,
                data_dir=self.config.data_dir,
                beam_config=self.config.study_raw.get("beam_screen_recording", {}),
            )
        )

        remote_cfg = self.config.study_raw.get("continuous_room_navigation", {})
        self.continuous_nav_client = ContinuousNavClient(
            host=str(remote_cfg.get("worker_host", "127.0.0.1")),
            port=int(remote_cfg.get("worker_port", 8765)),
        )
        self.continuous_nav_client.message_received.connect(
            self._on_continuous_nav_message
        )

        self._active_trial_backend = "none"
        self._activity_started = False
        self._observation_video_path: Path | None = None
        self._continuous_nav_feedback_timeout_seconds = float(
            remote_cfg.get("feedback_timeout_seconds", 10)
        )

        self.active_session: (
            Session | None
        ) = None

        self.active_trial: (
            Trial | None
        ) = None

        self.event_bus.publish(
            StudyEvent(
                event_type=
                    EventType.APP_STARTED
            )
        )

    # --------------------------------------------------

    def start_actor_critic_trial(
        self,
        session_id: str,
        condition: ExperimentCondition,
        practice: bool = False,
        use_maze_qinit: bool = False,
    ) -> Trial:

        if self.active_trial is not None:

            raise RuntimeError(
                f"Trial "
                f"{self.active_trial.trial_id} "
                f"is already active. "
                f"Stop it first."
            )

        session = (
            self.session_manager
            .get_session(
                session_id
            )
        )

        if session is None:

            raise ValueError(
                f"Session not found: "
                f"{session_id}"
            )

        if session.study not in (condition.study, Study.COMBINED_SESSION):
            raise ValueError(
                "Selected session and condition belong to incompatible study scopes"
            )

        trial = (
            self.trial_manager
            .create_trial(
                session,
                condition,
                practice=practice,
            )
        )

        try:

            self.rl_manager.prepare_actor_critic_trial(
                trial,
                use_maze_qinit=
                    use_maze_qinit,
            )

            self.active_session = session
            self.active_trial = trial
            self._active_trial_backend = "local_gridworld"
            self._activity_started = False
            self._publish_activity_prepared(trial)

            return trial

        except Exception:

            logger.exception(
                "Could not start trial %s",
                trial.trial_id,
            )

            try:

                self._stop_trial_sensor_recordings(
                    trial, reason="trial_start_failed"
                )
                self.trial_manager.end_trial(
                    trial,
                    completed=False,
                )

            except Exception:

                logger.exception(
                    "Could not mark failed "
                    "trial as stopped"
                )

            self.rl_manager.finalize_trial()

            self.active_trial = None

            raise

    def start_tracked_trial(
        self,
        session_id: str,
        condition: ExperimentCondition,
        practice: bool = False,
    ) -> Trial:

        """Start a persisted Trial without launching a console RL backend.

        Use this for protocol conditions whose simulator/device adapter is
        external or not integrated yet. The normal trial directory, metadata,
        database row, and lifecycle events are still created.
        """

        if self.active_trial is not None:
            raise RuntimeError(
                f"Trial {self.active_trial.trial_id} is already active. Stop it first."
            )

        session = self.session_manager.get_session(session_id)
        if session is None:
            raise ValueError(f"Session not found: {session_id}")

        if session.study not in (condition.study, Study.COMBINED_SESSION):
            raise ValueError(
                "Selected session and condition belong to incompatible study scopes"
            )

        trial = self.trial_manager.create_trial(
            session,
            condition,
            practice=practice,
        )

        self.active_session = session
        self.active_trial = trial
        self._active_trial_backend = "tracked"
        self._activity_started = False
        self._publish_activity_prepared(trial)
        return trial

    def prepare_observation_video_trial(
        self,
        session_id: str,
        condition: ExperimentCondition,
        video_path: Path,
        *,
        practice: bool = False,
    ) -> Trial:
        """Prepare a Study 3 MP4 without starting time or sensor recording."""
        if condition.study != Study.OBSERVATION:
            raise ValueError("Observation video trials must belong to Study 3")
        source = Path(video_path).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(f"Observation video not found: {source}")

        if self.active_trial is not None:
            raise RuntimeError(
                f"Trial {self.active_trial.trial_id} is already active. Stop it first."
            )
        session = self.session_manager.get_session(session_id)
        if session is None:
            raise ValueError(f"Session not found: {session_id}")
        if session.study not in (condition.study, Study.COMBINED_SESSION):
            raise ValueError(
                "Selected session and condition belong to incompatible study scopes"
            )

        trial = self.trial_manager.create_trial(session, condition, practice=practice)
        try:
            if trial.trial_path is not None:
                media_dir = trial.trial_path / "observation_video"
                media_dir.mkdir(parents=True, exist_ok=True)
                stat = source.stat()
                (media_dir / "source.json").write_text(
                    json.dumps(
                        {
                            "source_path": str(source),
                            "file_name": source.name,
                            "size_bytes": stat.st_size,
                            "modified_utc_timestamp": stat.st_mtime,
                            "playback_mode": "fullscreen",
                            "participant_start_required": True,
                        },
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            self.active_session = session
            self.active_trial = trial
            self._active_trial_backend = "observation_video"
            self._observation_video_path = source
            self._activity_started = False
            self._publish_activity_prepared(trial)
            return trial
        except Exception:
            try:
                self.trial_manager.end_trial(trial, completed=False)
            except Exception:
                logger.exception("Could not close failed observation-video trial")
            self.active_trial = None
            self.active_session = None
            self._active_trial_backend = "none"
            self._observation_video_path = None
            raise

    def observation_video_path_for_trial(self, trial_id: str) -> Path | None:
        trial = self.active_trial
        if (
            trial is None
            or trial.trial_id != trial_id
            or self._active_trial_backend != "observation_video"
        ):
            return None
        return self._observation_video_path

    def publish_observation_video_event(
        self, event_type: EventType, trial_id: str, value: str
    ) -> None:
        trial = self.active_trial
        if trial is None or trial.trial_id != trial_id:
            return
        self.event_bus.publish(
            StudyEvent(
                event_type=event_type,
                participant_id=trial.participant_code,
                session_id=trial.session_id,
                trial_id=trial.trial_id,
                value=value,
            )
        )

    def complete_active_observation_video(self, trial_id: str) -> bool:
        """Close the video trial and matching workflow run after EndOfMedia."""
        trial = self.active_trial
        if (
            trial is None
            or trial.trial_id != trial_id
            or trial.condition.study != Study.OBSERVATION
            or self._active_trial_backend != "observation_video"
            or not self._activity_started
        ):
            return False
        run = self.workflow_manager.has_active_run(trial.participant_code)
        if run is None or run.trial_id != trial_id:
            raise RuntimeError("Matching active Study 3 workflow run was not found")
        self.stop_active_trial(
            completed=True, collection_status=CollectionRunStatus.VALID
        )
        self.workflow_manager.end_run(
            run.run_id,
            completed=True,
            notes="Automatically completed when the observation video ended.",
            outcome=CollectionRunStatus.VALID,
        )
        return True

    def connect_continuous_nav_worker(
        self, host: str, port: int | None = None
    ) -> dict:
        """Connect/test the Ubuntu Study 1(b) worker and measure clock offset."""
        self.continuous_nav_client.configure(host, port)
        status = self.continuous_nav_client.connect_worker(measure_clock=True)
        return {
            "status": status,
            "clock_sync": self.continuous_nav_client.clock_sync,
        }

    def start_continuous_room_trial(
        self,
        session_id: str,
        condition: ExperimentCondition,
        *,
        practice: bool = False,
        hil_correction_length: int = 10,
        feedback_timeout_seconds: float = 10.0,
    ) -> Trial:
        """Start Study 1(b) on the Ubuntu worker with synchronized sensors."""
        from models.enums import Environment

        if condition.environment != Environment.CONTINUOUS_ROOM:
            raise ValueError("start_continuous_room_trial requires Continuous Room Navigation")
        if self.active_trial is not None:
            raise RuntimeError(
                f"Trial {self.active_trial.trial_id} is already active. Stop it first."
            )
        if not self.continuous_nav_client.connected:
            raise RuntimeError(
                "Ubuntu continuous-navigation worker is not connected. Connect/Test the worker first."
            )

        session = self.session_manager.get_session(session_id)
        if session is None:
            raise ValueError(f"Session not found: {session_id}")
        if session.study not in (condition.study, Study.COMBINED_SESSION):
            raise ValueError(
                "Selected session and condition belong to incompatible study scopes"
            )

        trial = self.trial_manager.create_trial(session, condition, practice=practice)
        self._continuous_nav_feedback_timeout_seconds = float(feedback_timeout_seconds)
        self.continuous_nav_client.set_active_trial(trial)
        try:
            if trial.trial_path is not None:
                import json
                config_path = trial.trial_path / "rl" / "continuous_nav_console_config.json"
                config_path.write_text(
                    json.dumps(
                        {
                            "worker_host": self.continuous_nav_client.host,
                            "worker_port": self.continuous_nav_client.port,
                            "hil_correction_length": int(hil_correction_length),
                            "feedback_timeout_seconds": float(feedback_timeout_seconds),
                            "clock_sync": self.continuous_nav_client.clock_sync,
                        },
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            # Prepare first so room geometry and the Ubuntu-side trial directory
            # are verified before physiological/video recording begins.
            self.continuous_nav_client.prepare_trial(
                trial,
                hil_correction_length=hil_correction_length,
                feedback_timeout_seconds=feedback_timeout_seconds,
            )

            self.active_session = session
            self.active_trial = trial
            self._active_trial_backend = "remote_continuous_room"
            self._activity_started = False
            self._publish_activity_prepared(trial)
            return trial
        except Exception:
            logger.exception("Could not start remote continuous-room trial %s", trial.trial_id)
            try:
                if self.continuous_nav_client.connected:
                    self.continuous_nav_client.stop_trial(
                        aborted=True, reason="trial_start_failed", timeout=5.0
                    )
            except Exception:
                logger.exception("Could not stop Ubuntu worker after trial-start failure")
            try:
                self._stop_trial_sensor_recordings(trial, reason="trial_start_failed")
                self.trial_manager.end_trial(trial, completed=False)
            except Exception:
                logger.exception("Could not mark failed remote trial as stopped")
            self.continuous_nav_client.clear_active_trial()
            self.active_trial = None
            self._active_trial_backend = "none"
            raise

    @property
    def activity_started(self) -> bool:
        """Whether the participant has released the prepared activity."""
        return self.active_trial is not None and self._activity_started

    def _publish_activity_prepared(self, trial: Trial) -> None:
        self.event_bus.publish(
            StudyEvent(
                event_type=EventType.ACTIVITY_PREPARED,
                participant_id=trial.participant_code,
                session_id=trial.session_id,
                trial_id=trial.trial_id,
                value=(
                    f"{trial.condition_code}/{trial.run_code}; "
                    "waiting_for_participant_start"
                ),
            )
        )

    def start_prepared_activity(self, trial_id: str) -> Trial:
        """Start a prepared activity after the participant presses Start.

        Trial time, the main-window countdown, sensor recordings, and the task
        backend all begin at this boundary.  Researcher-side preparation alone
        therefore never consumes protocol time or records waiting-room data.
        """
        trial = self.active_trial
        if trial is None or trial.trial_id != trial_id:
            raise RuntimeError("This activity is no longer waiting to start")
        if self._activity_started:
            return trial

        session = self.active_session
        if session is None:
            raise RuntimeError("The prepared activity has no active session")

        if getattr(getattr(trial, "condition", None), "study", None) == Study.OBSERVATION:
            missing: list[str] = []
            if not self.device_manager.hololens_stream_healthy():
                missing.append("HoloLens PV/EET")
            if not self.device_manager.shimmer_stream_healthy():
                missing.append("Shimmer GSR/PPG")
            if missing:
                raise RuntimeError(
                    "Study 3 cannot start because fresh data is unavailable from: "
                    + ", ".join(missing)
                    + ". Ask the researcher to reconnect/check the sensors, then try again."
                )

        try:
            if session.status == SessionStatus.CREATED:
                self.session_manager.start_session(session)

            self.trial_manager.start_trial(trial)
            # Mark the boundary immediately after TRIAL_STARTED so a backend
            # failure cannot cause a second click to start the same trial twice.
            self._activity_started = True
            self._start_trial_sensor_recordings(trial)

            if self._active_trial_backend == "local_gridworld":
                self.rl_manager.start()
            elif self._active_trial_backend == "remote_continuous_room":
                self.continuous_nav_client.start_trial()

            self.event_bus.publish(
                StudyEvent(
                    event_type=EventType.PARTICIPANT_ACTIVITY_STARTED,
                    participant_id=trial.participant_code,
                    session_id=trial.session_id,
                    trial_id=trial.trial_id,
                    value=f"{trial.condition_code}/{trial.run_code}",
                )
            )
            return trial
        except Exception:
            logger.exception(
                "Participant could not start prepared activity %s", trial.trial_id
            )
            self.event_bus.publish(
                StudyEvent(
                    event_type=EventType.EXPERIMENTER_NOTE,
                    participant_id=trial.participant_code,
                    session_id=trial.session_id,
                    trial_id=trial.trial_id,
                    value=(
                        "Participant Start Activity failed. Mark this run "
                        "Invalid/Repeat or Aborted after checking the task backend."
                    ),
                )
            )
            raise

    def begin_continuous_anytime_feedback(self) -> None:
        trial = self.active_trial
        if trial is None or self._active_trial_backend != "remote_continuous_room":
            raise RuntimeError("No continuous-navigation trial is active")
        if trial.condition.feedback_timing.value != "Anytime Feedback":
            raise RuntimeError("The active continuous trial is not in Anytime Feedback mode")
        timestamp_ns = self.continuous_nav_client.request_anytime_intervention()
        self.event_bus.publish(
            StudyEvent(
                event_type=EventType.ANYTIME_FEEDBACK_STARTED,
                participant_id=trial.participant_code,
                session_id=trial.session_id,
                trial_id=trial.trial_id,
                value=f"continuous_anytime_request; console_timestamp_utc_ns={timestamp_ns}",
            )
        )

    def send_continuous_nav_action(
        self, request_id: str, action: int | None, *, source_detail: str = "participant"
    ) -> None:
        trial = self.active_trial
        if trial is None or self._active_trial_backend != "remote_continuous_room":
            raise RuntimeError("No Study 1(b) continuous-navigation trial is active")
        timestamp_ns = self.continuous_nav_client.send_action(
            request_id,
            action,
            modality=trial.condition.modality.value,
            source_detail=source_detail,
        )
        self.event_bus.publish(
            StudyEvent(
                event_type=(
                    EventType.FEEDBACK_SKIPPED if action is None else EventType.FEEDBACK_RECEIVED
                ),
                participant_id=trial.participant_code,
                session_id=trial.session_id,
                trial_id=trial.trial_id,
                value=(
                    f"remote_request={request_id}; action={action}; "
                    f"modality={trial.condition.modality.value}; console_timestamp_utc_ns={timestamp_ns}; "
                    f"source={source_detail}"
                ),
            )
        )

    def _on_continuous_nav_message(self, msg: dict) -> None:
        """Mirror important Ubuntu events into the Console's master event stream."""
        trial = self.active_trial
        if trial is None:
            return
        remote_trial_id = msg.get("trial_id")
        if remote_trial_id and str(remote_trial_id) != trial.trial_id:
            return

        kind = str(msg.get("type") or "").upper()
        mapping = {
            "EPISODE_STARTED": EventType.EPISODE_STARTED,
            "EPISODE_ENDED": EventType.EPISODE_ENDED,
            "COLLISION": EventType.COLLISION,
            "HUMAN_ACTION_REQUEST": EventType.FEEDBACK_REQUESTED,
            "HUMAN_ACTION_APPLIED": EventType.FEEDBACK_APPLIED,
            "GOAL_REACHED": EventType.GOAL_REACHED,
        }
        event_type = mapping.get(kind)
        if event_type is not None:
            compact = {
                key: msg.get(key)
                for key in (
                    "intervention_id", "human_step", "human_total_steps",
                    "action", "reward", "done", "robot_x", "robot_y",
                    "robot_orientation", "goal_x", "goal_y",
                    "timestamp_utc_ns", "console_receive_timestamp_utc_ns"
                )
                if msg.get(key) is not None
            }
            self.event_bus.publish(
                StudyEvent(
                    event_type=event_type,
                    participant_id=trial.participant_code,
                    session_id=trial.session_id,
                    trial_id=trial.trial_id,
                    episode=msg.get("episode"),
                    step=msg.get("step"),
                    value=str(compact),
                )
            )
        elif kind in {
            "STATE_RESTORED", "HUMAN_CONTROL_STARTED", "HUMAN_CONTROL_ENDED",
            "RL_CONTROL_RESUMED", "TASK_STARTED", "TASK_ENDED", "TASK_STOPPED",
            "HUMAN_ACTION_TIMEOUT", "AGENT_BRIDGE_ERROR", "RL_PROCESS_LOG",
            "RL_PROCESS_FAILED", "TASK_ALREADY_STOPPED"
        }:
            self.event_bus.publish(
                StudyEvent(
                    event_type=EventType.EXPERIMENTER_NOTE,
                    participant_id=trial.participant_code,
                    session_id=trial.session_id,
                    trial_id=trial.trial_id,
                    episode=msg.get("episode"),
                    step=msg.get("step"),
                    value=f"Ubuntu {kind}: {msg}",
                )
            )

    def pause_active_trial(
        self,
    ) -> None:

        if self.active_trial is None:
            return
        if not self._activity_started:
            return
        if self._active_trial_backend == "remote_continuous_room":
            # Ubuntu worker v1 intentionally has no pause command; pausing only
            # the Console would desynchronize sensors and simulator time.
            return

        self.rl_manager.pause()
        self.trial_manager.pause_trial(self.active_trial)

    def resume_active_trial(
        self,
    ) -> None:

        if self.active_trial is None:
            return
        if not self._activity_started:
            return
        if self._active_trial_backend == "remote_continuous_room":
            return

        self.rl_manager.resume()
        self.trial_manager.resume_trial(self.active_trial)

    def stop_active_trial(
        self,
        completed: bool = True,
        *,
        collection_status: CollectionRunStatus | None = None,
        repeat_reason: str = "",
    ) -> None:

        if self.active_trial is None:
            return

        trial = self.active_trial
        backend = self._active_trial_backend
        activity_started = self._activity_started
        remote_error = ""

        # Stop the state-producing backend before publishing TRIAL_ENDED or
        # stopping HoloLens/Shimmer, so all task activity remains bracketed by
        # the synchronized sensor recording interval.
        if backend == "remote_continuous_room":
            try:
                self.continuous_nav_client.stop_trial(
                    aborted=(collection_status == CollectionRunStatus.ABORTED or not completed),
                    reason=repeat_reason or ("operator_completed" if completed else "operator_aborted"),
                )
            except Exception as exc:
                remote_error = str(exc)
                logger.exception("Could not stop Ubuntu continuous-navigation task cleanly")
        elif activity_started and backend not in ("tracked", "observation_video"):
            self.rl_manager.stop()

        self.trial_manager.end_trial(
            trial,
            completed=completed,
            collection_status=collection_status,
            repeat_reason=repeat_reason,
        )

        result = trial.collection_status.value.lower()
        self._stop_trial_sensor_recordings(trial, reason=f"trial_{result}")

        if backend == "remote_continuous_room":
            try:
                if self.continuous_nav_client.connected:
                    self.continuous_nav_client.finalize_trial()
                    bundle_dir = trial.trial_path / "rl" / "ubuntu" if trial.trial_path else None
                    if bundle_dir is not None:
                        bundle = self.continuous_nav_client.download_trial_bundle(bundle_dir)
                        self.event_bus.publish(
                            StudyEvent(
                                event_type=EventType.EXPERIMENTER_NOTE,
                                participant_id=trial.participant_code,
                                session_id=trial.session_id,
                                trial_id=trial.trial_id,
                                value=f"Ubuntu trial bundle downloaded and verified -> {bundle}",
                            )
                        )
            except Exception as exc:
                remote_error = (remote_error + "; " if remote_error else "") + str(exc)
                logger.exception("Could not finalize/download Ubuntu trial data")
            finally:
                self.continuous_nav_client.clear_active_trial()
        elif backend not in ("tracked", "observation_video"):
            self.rl_manager.finalize_trial()

        if remote_error:
            self.event_bus.publish(
                StudyEvent(
                    event_type=EventType.EXPERIMENTER_NOTE,
                    participant_id=trial.participant_code,
                    session_id=trial.session_id,
                    trial_id=trial.trial_id,
                    value=f"Ubuntu worker warning during trial close: {remote_error}",
                )
            )

        self.active_trial = None
        self._active_trial_backend = "none"
        self._activity_started = False
        self._observation_video_path = None

    def complete_active_trial_at_time_limit(
        self,
        trial_id: str,
        limit_seconds: int,
    ) -> bool:
        """Close the active experimental trial when its protocol timer expires.

        The trial and its workflow run are both finalized as valid.  A separate
        event is published after both records are closed so every researcher
        panel refreshes against a consistent, no-longer-running workflow state.
        """
        trial = self.active_trial
        if trial is None or trial.trial_id != trial_id or trial.practice:
            return False

        run = self.workflow_manager.has_active_run(trial.participant_code)
        if run is None or run.trial_id != trial_id:
            logger.error(
                "Cannot complete timed trial %s: matching active workflow run not found",
                trial_id,
            )
            return False

        participant_code = trial.participant_code
        session_id = trial.session_id
        readable_label = trial.readable_run_label
        limit_minutes = limit_seconds / 60
        minutes_text = (
            str(int(limit_minutes))
            if limit_minutes.is_integer()
            else f"{limit_minutes:g}"
        )
        note = f"Automatically completed at the {minutes_text}-minute protocol time limit."

        self.stop_active_trial(
            completed=True,
            collection_status=CollectionRunStatus.VALID,
        )
        self.workflow_manager.end_run(
            run.run_id,
            completed=True,
            notes=note,
            outcome=CollectionRunStatus.VALID,
        )
        self.event_bus.publish(
            StudyEvent(
                event_type=EventType.TRIAL_TIME_LIMIT_REACHED,
                participant_id=participant_code,
                session_id=session_id,
                trial_id=trial_id,
                value=f"{readable_label}; limit_seconds={limit_seconds}; marked Valid",
            )
        )
        return True

    # --------------------------------------------------
    # Experimental sensor recording lifecycle

    def _start_trial_sensor_recordings(self, trial: Trial) -> None:
        """Attach connected sensors to the active readable R## directory.

        Beam records Training, Study 1, and Study 2. HoloLens records only the
        Agent Observation phase (including its optional familiarization run).
        Shimmer keeps the experimental-only policy. Study 3 performs an extra
        required-sensor check at participant Start; earlier phases retain the
        non-blocking sensor policy.
        """

        eye_tracker = eye_tracker_for_trial(trial)
        use_hololens = eye_tracker == DeviceType.HOLOLENS
        if use_hololens and self.device_manager.hololens_stream_healthy():
            try:
                paths = self.device_manager.start_hololens_trial_recording(trial)
                self.event_bus.publish(
                    StudyEvent(
                        event_type=EventType.RECORDING_STARTED,
                        participant_id=trial.participant_code,
                        session_id=trial.session_id,
                        trial_id=trial.trial_id,
                        value=(
                            "HoloLens PV+EET -> "
                            f"{paths['video']} ; pointer={paths['pointer_csv']} ; "
                            f"raw_eet={paths['eet_csv']}"
                        ),
                    )
                )
            except Exception as exc:
                logger.exception("Could not start HoloLens recording for %s", trial.trial_id)
                self.event_bus.publish(
                    StudyEvent(
                        event_type=EventType.EXPERIMENTER_NOTE,
                        participant_id=trial.participant_code,
                        session_id=trial.session_id,
                        trial_id=trial.trial_id,
                        value=f"HoloLens recording NOT started: {exc}",
                    )
                )
                if trial.condition.study == Study.OBSERVATION:
                    raise RuntimeError(
                        f"Required Study 3 HoloLens recording could not start: {exc}"
                    ) from exc
        elif use_hololens:
            self.event_bus.publish(
                StudyEvent(
                    event_type=EventType.EXPERIMENTER_NOTE,
                    participant_id=trial.participant_code,
                    session_id=trial.session_id,
                    trial_id=trial.trial_id,
                    value=(
                        "HoloLens recording NOT started: fresh PV + EET streams "
                        "were not available when this activity began."
                    ),
                )
            )

        use_beam = eye_tracker == DeviceType.BEAM
        if use_beam and self.device_manager.beam_stream_healthy():
            try:
                paths = self.device_manager.start_beam_trial_recording(trial)
                self.event_bus.publish(
                    StudyEvent(
                        event_type=EventType.RECORDING_STARTED,
                        participant_id=trial.participant_code,
                        session_id=trial.session_id,
                        trial_id=trial.trial_id,
                        value=(
                            f"Beam screen gaze -> {paths['gaze_csv']} ; "
                            f"screen_gaze_video={paths['screen_video']} ; "
                            f"capture_viewport={paths.get('capture_viewport')} ; "
                            f"capture_source={paths.get('capture_target_source')}"
                        ),
                    )
                )
                if paths.get("screen_video_error"):
                    self.event_bus.publish(
                        StudyEvent(
                            event_type=EventType.EXPERIMENTER_NOTE,
                            participant_id=trial.participant_code,
                            session_id=trial.session_id,
                            trial_id=trial.trial_id,
                            value=(
                                "Beam gaze CSV is recording, but screen_gaze.mp4 "
                                f"could not start: {paths['screen_video_error']}"
                            ),
                        )
                    )
            except Exception as exc:
                logger.exception("Could not start Beam recording for %s", trial.trial_id)
                self.event_bus.publish(
                    StudyEvent(
                        event_type=EventType.EXPERIMENTER_NOTE,
                        participant_id=trial.participant_code,
                        session_id=trial.session_id,
                        trial_id=trial.trial_id,
                        value=f"Beam recording NOT started: {exc}",
                    )
                )
        elif use_beam:
            self.event_bus.publish(
                StudyEvent(
                    event_type=EventType.EXPERIMENTER_NOTE,
                    participant_id=trial.participant_code,
                    session_id=trial.session_id,
                    trial_id=trial.trial_id,
                    value=(
                        "Beam recording NOT started: fresh webcam eye-tracking "
                        "data was not available when this activity began."
                    ),
                )
            )

        # Preserve the previous Shimmer policy: experimental trials only.
        if trial.practice:
            return

        if not self.device_manager.shimmer_stream_healthy():
            message = (
                "Shimmer physiological recording NOT started: no fresh GSR/PPG "
                "stream was available when the experimental trial began."
            )
            logger.warning("%s trial=%s", message, trial.trial_id)
            self.event_bus.publish(
                StudyEvent(
                    event_type=EventType.EXPERIMENTER_NOTE,
                    participant_id=trial.participant_code,
                    session_id=trial.session_id,
                    trial_id=trial.trial_id,
                    value=message,
                )
            )
            return

        path = self.device_manager.start_shimmer_trial_recording(trial)
        self.event_bus.publish(
            StudyEvent(
                event_type=EventType.RECORDING_STARTED,
                participant_id=trial.participant_code,
                session_id=trial.session_id,
                trial_id=trial.trial_id,
                value=f"Shimmer GSR+PPG -> {path}",
            )
        )

    def _stop_trial_sensor_recordings(self, trial: Trial, reason: str) -> None:
        beam_summary = self.device_manager.stop_beam_trial_recording(
            trial_id=trial.trial_id,
            reason=reason,
        )
        if beam_summary is not None:
            self.event_bus.publish(
                StudyEvent(
                    event_type=EventType.RECORDING_STOPPED,
                    participant_id=trial.participant_code,
                    session_id=trial.session_id,
                    trial_id=trial.trial_id,
                    value=(
                        f"Beam screen gaze: {beam_summary['sample_count']} samples, "
                        f"{beam_summary['valid_sample_count']} valid -> "
                        f"{beam_summary['recording_dir']} ; "
                        f"screen_video_frames={beam_summary['video_frame_count']} ; "
                        f"screen_video={beam_summary['screen_video_path']} ; "
                        f"capture_error={beam_summary['video_capture_error'] or 'none'}"
                    ),
                )
            )

        holo_summary = self.device_manager.stop_hololens_trial_recording(
            trial_id=trial.trial_id,
            reason=reason,
        )
        if holo_summary is not None:
            self.event_bus.publish(
                StudyEvent(
                    event_type=EventType.RECORDING_STOPPED,
                    participant_id=trial.participant_code,
                    session_id=trial.session_id,
                    trial_id=trial.trial_id,
                    value=(
                        f"HoloLens PV+EET: {holo_summary['video_frame_count']} video frames, "
                        f"{holo_summary['eet_row_count']} EET samples -> "
                        f"{holo_summary['recording_dir']}"
                    ),
                )
            )

        shimmer_summary = self.device_manager.stop_shimmer_trial_recording(
            trial_id=trial.trial_id,
            reason=reason,
        )
        if shimmer_summary is not None:
            self.event_bus.publish(
                StudyEvent(
                    event_type=EventType.RECORDING_STOPPED,
                    participant_id=trial.participant_code,
                    session_id=trial.session_id,
                    trial_id=trial.trial_id,
                    value=(
                        f"Shimmer GSR+PPG: {shimmer_summary['sample_count']} samples -> "
                        f"{shimmer_summary['path']}"
                    ),
                )
            )

    # --------------------------------------------------

    def shutdown(self) -> None:

        logger.info(
            "Shutting down "
            "HINT Study Console"
        )

        if self.active_trial is not None:

            try:

                self.stop_active_trial(
                    completed=False
                )

            except Exception:

                logger.exception(
                    "Error while stopping "
                    "active trial during "
                    "shutdown"
                )

        try:
            self.continuous_nav_client.disconnect_worker()
        except Exception:
            logger.exception("Error while disconnecting Ubuntu continuous-navigation worker")

        try:
            self.device_manager.disconnect_all()
        except Exception:
            logger.exception("Error while disconnecting devices during shutdown")

        self.db.close()
