"""Main researcher-console window -- IRB-aligned V0.6 workflow nav.

Three destinations instead of the original eight:

    Devices   -- connect study devices, see live status
    Workflow  -- register/select a participant and step through
                 Registration -> Study 1 Training -> Study 1 Study ->
                 Study 2 Training -> Study 2 Study, with each step showing
                 Not Started / In Progress / Completed and a repeatable
                 "run again" flow
    Event Log -- live event feed + disk usage, for monitoring/debugging

The old Participants / Study Setup / Live Session pages' functionality now
lives inside the Workflow page's step panels (see gui/workflow_page.py,
gui/study1_step_panel.py, gui/study2_step_panel.py, gui/registration_panel.py)
rather than as separate top-level destinations.
"""

from __future__ import annotations

import math
import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QStatusBar,
    QWidget,
)

from core.application_controller import ApplicationController
from gui.devices_page import DevicesPage
from gui.continuous_nav_window import ContinuousNavParticipantWindow
from gui.event_log_page import EventLogPage
from gui.participant_window import ParticipantWindow
from gui.observation_video_window import ObservationVideoWindow
from gui.storage_location_dialog import ensure_storage_location
from gui.workflow_page import WorkflowPage
from core.config_loader import PROJECT_ROOT
from models.enums import EventType, Study
from models.event import StudyEvent

_NAV_ITEMS = ["Devices", "Workflow", "Event Log"]

_DEVICES_INDEX = 0
_WORKFLOW_INDEX = 1
_EVENT_LOG_INDEX = 2

_DEFAULT_STUDY_TIMER_MINUTES = {
    Study.STUDY_1: 8,
    Study.STUDY_2: 8,
    Study.OBSERVATION: 5,
}

_STUDY_TIMER_CONFIG_KEYS = {
    Study.STUDY_1: "study_1_condition_minutes",
    Study.STUDY_2: "study_2_condition_minutes",
    Study.OBSERVATION: "observation_condition_minutes",
}


class MainWindow(QMainWindow):
    def __init__(self, controller: ApplicationController) -> None:
        super().__init__()
        self._controller = controller

        # Participant-facing windows are prepared by the researcher, then wait
        # for the participant's own Start Activity confirmation before a trial
        # clock, recording, or task backend begins.
        self._participant_window = ParticipantWindow(controller)
        self._continuous_nav_window = ContinuousNavParticipantWindow(controller)
        self._observation_video_window = ObservationVideoWindow(controller)

        self.setWindowTitle("HINT Study Console")
        self.resize(1280, 840)

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.setCentralWidget(central)

        self._nav_list = QListWidget()
        self._nav_list.setFixedWidth(160)
        for name in _NAV_ITEMS:
            QListWidgetItem(name, self._nav_list)
        self._nav_list.currentRowChanged.connect(self._on_nav_changed)
        layout.addWidget(self._nav_list)

        self._stack = QStackedWidget()
        layout.addWidget(self._stack)

        self._build_pages()

        # Start on Devices: connect study hardware before registering
        # participants and running steps.
        self._nav_list.setCurrentRow(_DEVICES_INDEX)

        status_bar = QStatusBar()
        status_bar.showMessage(
            f"Mode: {controller.config.mode.value}    |    Study config: {controller.config.study_version}"
        )
        data_location_name = (
            controller.config.data_dir.name
            or controller.config.data_dir.anchor
            or str(controller.config.data_dir)
        )
        self._storage_button = QPushButton(f"Data: {data_location_name}")
        self._storage_button.setToolTip(
            f"Current data location: {controller.config.data_dir}\n"
            "Click to choose a different location for the next app launch."
        )
        self._storage_button.clicked.connect(self._change_data_location)
        status_bar.addPermanentWidget(self._storage_button)
        self._timer_label = QLabel("Activity time: --:--")
        self._timer_label.setMinimumWidth(310)
        self._timer_label.setStyleSheet(
            "font-size: 18px; font-weight: bold; padding: 3px 10px; "
            "border-left: 1px solid #bbb;"
        )
        status_bar.addPermanentWidget(self._timer_label)
        self.setStatusBar(status_bar)

        self._timer_tick = QTimer(self)
        self._timer_tick.setInterval(250)
        self._timer_tick.timeout.connect(self._update_study_timer)
        self._timed_trial_id: str | None = None
        self._prepared_trial_id: str | None = None
        self._timed_study: Study | None = None
        self._timer_limit_seconds = 0
        self._timer_remaining_seconds = 0.0
        self._timer_deadline = 0.0
        self._timer_paused = False
        self._timer_expiring = False
        # Activity elapsed time is shown for every participant activity,
        # including practice/training and Study 3.  The existing Study 1/2
        # protocol countdown remains separate and retains its auto-stop logic.
        self._activity_elapsed_seconds = 0.0
        self._activity_resume_monotonic = 0.0
        controller.event_bus.event_published.connect(self._on_study_timer_event)

    def _build_pages(self) -> None:
        self._stack.addWidget(DevicesPage(self._controller))  # 0
        self._workflow_page = WorkflowPage(
            self._controller, on_manage_devices=self._go_to_devices
        )
        self._stack.addWidget(self._workflow_page)  # 1
        self._stack.addWidget(EventLogPage(self._controller))  # 2

    def _change_data_location(self) -> None:
        selected = ensure_storage_location(
            self._controller.config.config_dir,
            PROJECT_ROOT,
            force_prompt=True,
            parent=self,
        )
        if selected is None:
            return
        if selected == self._controller.config.data_dir.resolve():
            QMessageBox.information(
                self,
                "Data location unchanged",
                f"The console is already using:\n{selected}",
            )
            return
        QMessageBox.information(
            self,
            "Restart required",
            f"The new data location has been saved:\n{selected}\n\n"
            "Finish or abort any active run, close the console, and reopen it. "
            "The current launch will continue using the old location.",
        )

    def _duration_seconds_for_study(self, study: Study) -> int:
        timing = self._controller.config.study_raw.get("timing", {})
        key = _STUDY_TIMER_CONFIG_KEYS[study]
        minutes = float(timing.get(key, _DEFAULT_STUDY_TIMER_MINUTES[study]))
        return max(1, int(round(minutes * 60)))

    @staticmethod
    def _format_remaining(seconds: float) -> str:
        total = max(0, int(math.ceil(seconds)))
        return f"{total // 60:02d}:{total % 60:02d}"

    @staticmethod
    def _format_elapsed(seconds: float) -> str:
        total = max(0, int(seconds))
        return f"{total // 60:02d}:{total % 60:02d}"

    def _current_activity_elapsed(self) -> float:
        elapsed = self._activity_elapsed_seconds
        if self._timed_trial_id is not None and not self._timer_paused:
            elapsed += max(0.0, time.monotonic() - self._activity_resume_monotonic)
        return elapsed

    def _set_timer_label(self, remaining: float | None = None, *, paused: bool = False) -> None:
        elapsed_text = self._format_elapsed(self._current_activity_elapsed())
        state = " (paused)" if paused else ""

        if self._timer_limit_seconds > 0 and remaining is not None:
            study = self._timed_study.value if self._timed_study is not None else "Study"
            self._timer_label.setText(
                f"Activity elapsed{state}: {elapsed_text}  |  "
                f"{study} remaining: {self._format_remaining(remaining)}"
            )
            if remaining <= 10:
                color = "#b71c1c"
            elif remaining <= 60:
                color = "#b35a00"
            else:
                color = "#1b5e20"
        else:
            self._timer_label.setText(f"Activity elapsed{state}: {elapsed_text}")
            color = "#1b5e20"

        self._timer_label.setStyleSheet(
            "font-size: 18px; font-weight: bold; padding: 3px 10px; "
            f"border-left: 1px solid #bbb; color: {color};"
        )

    def _start_study_timer(self, trial_id: str) -> None:
        trial = self._controller.trial_manager.get_trial(trial_id)
        if trial is None:
            self._clear_study_timer()
            return

        self._timed_trial_id = trial_id
        self._timed_study = trial.condition.study
        self._timer_paused = False
        self._timer_expiring = False
        self._activity_elapsed_seconds = 0.0
        self._activity_resume_monotonic = time.monotonic()

        # Preserve the existing protocol countdown/automatic completion only
        # for real Study 1 and Study 2 trials. Practice/training and Study 3
        # now get an elapsed activity clock without changing their lifecycle.
        protocol_timed = (
            not trial.practice
            and trial.condition.study in (Study.STUDY_1, Study.STUDY_2)
        )
        if protocol_timed:
            self._timer_limit_seconds = self._duration_seconds_for_study(self._timed_study)
            self._timer_remaining_seconds = float(self._timer_limit_seconds)
            self._timer_deadline = time.monotonic() + self._timer_remaining_seconds
            self._set_timer_label(self._timer_remaining_seconds)
        else:
            self._timer_limit_seconds = 0
            self._timer_remaining_seconds = 0.0
            self._timer_deadline = 0.0
            self._set_timer_label()

        self._timer_tick.start()

    def _pause_study_timer(self) -> None:
        if self._timed_trial_id is None or self._timer_paused:
            return
        now = time.monotonic()
        self._activity_elapsed_seconds += max(0.0, now - self._activity_resume_monotonic)
        if self._timer_limit_seconds > 0:
            self._timer_remaining_seconds = max(0.0, self._timer_deadline - now)
        self._timer_paused = True
        self._timer_tick.stop()
        self._set_timer_label(
            self._timer_remaining_seconds if self._timer_limit_seconds > 0 else None,
            paused=True,
        )

    def _resume_study_timer(self) -> None:
        if self._timed_trial_id is None or not self._timer_paused:
            return
        self._activity_resume_monotonic = time.monotonic()
        if self._timer_limit_seconds > 0:
            self._timer_deadline = time.monotonic() + self._timer_remaining_seconds
        self._timer_paused = False
        self._set_timer_label(
            self._timer_remaining_seconds if self._timer_limit_seconds > 0 else None
        )
        self._timer_tick.start()

    def _clear_study_timer(self) -> None:
        self._timer_tick.stop()
        self._timed_trial_id = None
        self._prepared_trial_id = None
        self._timed_study = None
        self._timer_limit_seconds = 0
        self._timer_remaining_seconds = 0.0
        self._timer_deadline = 0.0
        self._timer_paused = False
        self._timer_expiring = False
        self._activity_elapsed_seconds = 0.0
        self._activity_resume_monotonic = 0.0
        self._timer_label.setText("Activity time: --:--")
        self._timer_label.setStyleSheet(
            "font-size: 18px; font-weight: bold; padding: 3px 10px; "
            "border-left: 1px solid #bbb;"
        )

    def _update_study_timer(self) -> None:
        if self._timed_trial_id is None or self._timer_paused or self._timer_expiring:
            return

        # Every activity gets an elapsed clock. Only Study 1/2 experimental
        # trials additionally maintain the pre-existing countdown and auto-stop.
        if self._timer_limit_seconds <= 0:
            self._set_timer_label()
            return

        self._timer_remaining_seconds = max(0.0, self._timer_deadline - time.monotonic())
        self._set_timer_label(self._timer_remaining_seconds)
        if self._timer_remaining_seconds > 0:
            return

        self._timer_expiring = True
        self._timer_tick.stop()
        trial_id = self._timed_trial_id
        study_name = self._timed_study.value if self._timed_study else "Study"
        completed = self._controller.complete_active_trial_at_time_limit(
            trial_id,
            self._timer_limit_seconds,
        )
        if completed:
            QMessageBox.information(
                self,
                f"{study_name} time complete",
                "The scheduled time has elapsed. The run was stopped and marked valid.",
            )
        else:
            QMessageBox.warning(
                self,
                "Could not close timed run",
                "The countdown reached zero, but the active trial could not be finalized. "
                "Please use the study controls to finish or abort it.",
            )
            self._clear_study_timer()

    def _on_study_timer_event(self, event: StudyEvent) -> None:
        if event.event_type == EventType.ACTIVITY_PREPARED and event.trial_id:
            self._prepared_trial_id = event.trial_id
            self._timer_label.setText("Activity time: waiting for participant")
        elif event.event_type == EventType.TRIAL_STARTED and event.trial_id:
            self._prepared_trial_id = None
            self._start_study_timer(event.trial_id)
        elif event.event_type == EventType.TRIAL_PAUSED and event.trial_id == self._timed_trial_id:
            self._pause_study_timer()
        elif event.event_type == EventType.TRIAL_RESUMED and event.trial_id == self._timed_trial_id:
            self._resume_study_timer()
        elif event.event_type == EventType.TRIAL_ENDED:
            if event.trial_id == self._timed_trial_id:
                self._clear_study_timer()
            elif event.trial_id == self._prepared_trial_id:
                self._prepared_trial_id = None
                self._clear_study_timer()

    def _go_to_devices(self) -> None:
        self._nav_list.setCurrentRow(_DEVICES_INDEX)

    def _on_nav_changed(self, index: int) -> None:
        if index >= 0:
            self._stack.setCurrentIndex(index)

    def closeEvent(self, event) -> None:
        self._timer_tick.stop()
        self._controller.shutdown()
        self._participant_window.close()
        self._continuous_nav_window.close()
        self._observation_video_window.close()
        super().closeEvent(event)
