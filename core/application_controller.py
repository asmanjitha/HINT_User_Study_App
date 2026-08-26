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
        return trial

    def pause_active_trial(
        self,
    ) -> None:

        if self.active_trial is None:
            return

        self.rl_manager.pause()

        self.trial_manager.pause_trial(
            self.active_trial
        )

    def resume_active_trial(
        self,
    ) -> None:

        if self.active_trial is None:
            return

        self.rl_manager.resume()

        self.trial_manager.resume_trial(
            self.active_trial
        )

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

        self.rl_manager.stop()

        # Keep recorder alive until TRIAL_ENDED has been published. The
        # collection status tells analysis whether this R## is valid, invalid,
        # or aborted without deleting any data.
        self.trial_manager.end_trial(
            trial,
            completed=completed,
            collection_status=collection_status,
            repeat_reason=repeat_reason,
        )

        result = trial.collection_status.value.lower()
        self._stop_trial_sensor_recordings(
            trial,
            reason=f"trial_{result}",
        )

        self.rl_manager.finalize_trial()

        self.active_trial = None

    # --------------------------------------------------
    # Experimental sensor recording lifecycle

    def _start_trial_sensor_recordings(self, trial: Trial) -> None:
        """Automatically attach live Shimmer GSR/PPG to Study 1/2 trials.

        Practice/training trials are intentionally excluded so familiarization
        recordings do not get mixed with the primary experimental data.  If the
        researcher explicitly chose to start a study while Shimmer was not live,
        the trial is allowed to continue but an event records that physiology was
        unavailable.
        """

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
        summary = self.device_manager.stop_shimmer_trial_recording(
            trial_id=trial.trial_id,
            reason=reason,
        )
        if summary is None:
            return
        self.event_bus.publish(
            StudyEvent(
                event_type=EventType.RECORDING_STOPPED,
                participant_id=trial.participant_code,
                session_id=trial.session_id,
                trial_id=trial.trial_id,
                value=(
                    f"Shimmer GSR+PPG: {summary['sample_count']} samples -> "
                    f"{summary['path']}"
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
            self.device_manager.disconnect_all()
        except Exception:
            logger.exception("Error while disconnecting devices during shutdown")

        self.db.close()