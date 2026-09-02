"""Application controller."""

from __future__ import annotations

import logging

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

            if (
                session.status
                == SessionStatus.CREATED
            ):

                self.session_manager.start_session(
                    session
                )

            self.trial_manager.start_trial(
                trial
            )

            self._start_trial_sensor_recordings(trial)

            self.active_session = session

            self.active_trial = trial

            self.rl_manager.start()
            self._active_trial_backend = "local_gridworld"

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

        if session.status == SessionStatus.CREATED:
            self.session_manager.start_session(session)

        try:
            self.trial_manager.start_trial(trial)
            self._start_trial_sensor_recordings(trial)
        except Exception:
            logger.exception("Could not start tracked trial %s", trial.trial_id)
            self._stop_trial_sensor_recordings(trial, reason="trial_start_failed")
            try:
                self.trial_manager.end_trial(trial, completed=False)
            except Exception:
                logger.exception("Could not mark failed tracked trial as stopped")
            raise

        self.active_session = session
        self.active_trial = trial
        self._active_trial_backend = "tracked"
        return trial

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

            if session.status == SessionStatus.CREATED:
                self.session_manager.start_session(session)
            self.trial_manager.start_trial(trial)
            self._start_trial_sensor_recordings(trial)
            self.active_session = session
            self.active_trial = trial
            self._active_trial_backend = "remote_continuous_room"

            # Only now allow the Ubuntu RL process to move: sensors and the
            # master trial lifecycle are already recording against the same T/R id.
            self.continuous_nav_client.start_trial()
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
        else:
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
        else:
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

        HoloLens PV + EET is recorded for BOTH training/practice and primary
        study trials. Shimmer keeps the existing experimental-only policy so
        familiarization physiology is not mixed with primary physiology data.
        Sensor unavailability never prevents the trial from running; it is
        recorded as an experimenter-note event instead.
        """

        # HoloLens: requested for every training/study activity when the stream
        # is live. This creates sensors/hololens inside the current R## folder.
        if self.device_manager.hololens_stream_healthy():
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
        else:
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
