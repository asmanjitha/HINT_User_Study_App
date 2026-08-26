"""Study 2 training/familiarization session panel.

The experimental Study 2 modality matrix lives in ``study2_study_panel.py``.
This panel intentionally preserves the existing tracked training-session UI.
"""

from __future__ import annotations

import datetime

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.application_controller import ApplicationController
from devices.voice_recognizer import VoiceCommandRecognizer
from core.workflow_manager import (
    STEP_LABELS,
    STUDY1_STUDY_REQUIRED_CONDITION_COUNT,
)
from models.enums import (
    Environment,
    FeedbackTiming,
    Modality,
    StepOverallStatus,
    Study,
    WorkflowStep,
)
from models.trial import ExperimentCondition


def _fmt_time(ts: float | None) -> str:
    if not ts:
        return "--"
    return datetime.datetime.fromtimestamp(ts).strftime("%H:%M:%S")


def _fmt_duration(started: float | None, ended: float | None) -> str:
    if not started or not ended:
        return "--"
    seconds = int(ended - started)
    return f"{seconds // 60}m {seconds % 60}s"


_STUDY2_ENVIRONMENTS = [
    Environment.GRIDWORLD,
    Environment.CONTINUOUS_ROOM,
    Environment.HUMAN_AGENT_BASELINE,
]

_STUDY2_MODALITIES = [Modality.KEYBOARD, Modality.JOYSTICK, Modality.VOICE, Modality.IMPLICIT]


class Study2StepPanel(QWidget):
    def __init__(
        self,
        controller: ApplicationController,
        step: WorkflowStep,
        on_step_changed,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if step != WorkflowStep.STUDY2_TRAINING:
            raise ValueError("Study2StepPanel is the Study 2 Training panel only")
        self._controller = controller
        self._step = step
        self._on_step_changed = on_step_changed

        self._participant_code: str | None = None
        self._active_run = None  # models.workflow.StepRun
        self._active_trial_id: str | None = None

        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.timeout.connect(self._tick_elapsed)

        root = QVBoxLayout(self)

        title = QLabel(STEP_LABELS[step])
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        root.addWidget(title)

        note = QLabel(
            "Study 2 Training remains the existing familiarization/session-tracking "
            "window. Use it to practice the relevant environment and feedback modality "
            "before the experimental Study 2 modality conditions."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #666; font-size: 11px;")
        root.addWidget(note)

        root.addWidget(self._build_config_box())
        root.addWidget(self._build_status_box())
        root.addWidget(self._build_history_box(), 1)

    def _build_config_box(self) -> QGroupBox:
        box = QGroupBox("Configuration (recorded with the session)")
        form = QFormLayout(box)

        self._env_combo = QComboBox()
        for env in _STUDY2_ENVIRONMENTS:
            self._env_combo.addItem(env.value, env.name)
        form.addRow("Environment:", self._env_combo)

        self._modality_combo = QComboBox()
        for modality in _STUDY2_MODALITIES:
            self._modality_combo.addItem(modality.value, modality.name)
        form.addRow("Feedback modality:", self._modality_combo)

        self._timing_combo = QComboBox()
        for timing in (FeedbackTiming.REQUESTED, FeedbackTiming.ANYTIME):
            self._timing_combo.addItem(timing.value, timing.name)
        form.addRow("Feedback timing:", self._timing_combo)

        return box

    def _build_status_box(self) -> QGroupBox:
        box = QGroupBox("Run")
        layout = QVBoxLayout(box)

        self._status_label = QLabel("No run in progress.")
        layout.addWidget(self._status_label)

        self._elapsed_label = QLabel("Elapsed: --")
        layout.addWidget(self._elapsed_label)

        self._notes_input = QPlainTextEdit()
        self._notes_input.setPlaceholderText("Session notes (optional)...")
        self._notes_input.setFixedHeight(60)
        layout.addWidget(self._notes_input)

        buttons = QHBoxLayout()
        self._start_btn = QPushButton("Start Session")
        self._start_btn.clicked.connect(self._start_run)

        self._complete_btn = QPushButton("Mark Complete")
        self._complete_btn.clicked.connect(lambda: self._stop_run(completed=True))

        self._abort_btn = QPushButton("Abort")
        self._abort_btn.clicked.connect(lambda: self._stop_run(completed=False))

        for btn in (self._start_btn, self._complete_btn, self._abort_btn):
            buttons.addWidget(btn)
        layout.addLayout(buttons)

        self._set_running_controls(False)
        return box

    def _build_history_box(self) -> QGroupBox:
        box = QGroupBox("Run History")
        layout = QVBoxLayout(box)
        self._history_table = QTableWidget(0, 5)
        self._history_table.setHorizontalHeaderLabels(["Run", "Status", "Started", "Ended", "Duration"])
        self._history_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._history_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self._history_table)
        return box

    # -- Public API -----------------------------------------------------------
    def set_participant(self, participant_code: str | None) -> None:
        self._participant_code = participant_code
        self.refresh()

    def refresh(self) -> None:
        if self._participant_code is None:
            self._status_label.setText("Select or register a participant first.")
            self._set_running_controls(False)
            self._start_btn.setEnabled(False)
            self._history_table.setRowCount(0)
            self._elapsed_timer.stop()
            return

        summary = self._controller.workflow_manager.step_status(self._participant_code, self._step)
        self._active_run = summary.active_run
        self._active_trial_id = (
            None if self._active_run is None else self._active_run.trial_id
        )
        blocking_run = self._controller.workflow_manager.has_active_run(self._participant_code)

        if self._active_run is not None:
            self._status_label.setText(f"In progress: {self._active_run.run_id}")
            self._set_running_controls(True)
            self._start_btn.setEnabled(False)
            self._elapsed_timer.start(1000)
            self._tick_elapsed()
        else:
            self._elapsed_timer.stop()
            self._elapsed_label.setText("Elapsed: --")
            self._status_label.setText(
                f"{summary.completed_count} completed run(s) so far." if summary.total_runs else "Not started yet."
            )
            self._set_running_controls(False)

            study1_summary = self._controller.workflow_manager.step_status(
                self._participant_code, WorkflowStep.STUDY1_STUDY
            )
            study1_complete = (
                study1_summary.overall_status == StepOverallStatus.COMPLETED
            )

            self._start_btn.setEnabled(
                blocking_run is None and study1_complete
            )

            if blocking_run is not None:
                self._status_label.setText(
                    f"Another run ({blocking_run.run_id}) is in progress for this participant. "
                    "Finish or abort it first."
                )
            elif not study1_complete:
                self._status_label.setText(
                    f"Study 1 is not complete: {study1_summary.completed_count}/"
                    f"{STUDY1_STUDY_REQUIRED_CONDITION_COUNT} required conditions finished. "
                    "Complete the Study 1 protocol sub-steps before starting Study 2."
                )

        self._refresh_history()

    def _refresh_history(self) -> None:
        runs = self._controller.workflow_manager.list_runs(self._participant_code, self._step)
        self._history_table.setRowCount(len(runs))
        for row, run in enumerate(reversed(runs)):
            self._history_table.setItem(row, 0, QTableWidgetItem(run.run_id))
            self._history_table.setItem(row, 1, QTableWidgetItem(run.status.value))
            self._history_table.setItem(row, 2, QTableWidgetItem(_fmt_time(run.started_at)))
            self._history_table.setItem(row, 3, QTableWidgetItem(_fmt_time(run.ended_at)))
            self._history_table.setItem(row, 4, QTableWidgetItem(_fmt_duration(run.started_at, run.ended_at)))

    # -- Actions ---------------------------------------------------------------
    def _start_run(self) -> None:
        if self._participant_code is None:
            return

        study1_summary = self._controller.workflow_manager.step_status(
            self._participant_code, WorkflowStep.STUDY1_STUDY
        )
        if study1_summary.overall_status != StepOverallStatus.COMPLETED:
            QMessageBox.information(
                self,
                "Study 1 incomplete",
                f"This participant has completed {study1_summary.completed_count}/"
                f"{STUDY1_STUDY_REQUIRED_CONDITION_COUNT} required Study 1 conditions. "
                "Complete all Study 1 protocol conditions before starting Study 2.",
            )
            return

        environment = Environment[self._env_combo.currentData()]
        modality = Modality[self._modality_combo.currentData()]
        timing = FeedbackTiming[self._timing_combo.currentData()]

        if environment == Environment.GRIDWORLD and modality == Modality.VOICE:
            ok, message = self._controller.device_manager.check_microphone()
            if not ok:
                QMessageBox.warning(
                    self,
                    "Voice microphone unavailable",
                    "Voice Gridworld training needs a connected microphone that is "
                    f"receiving data.\n\n{message}",
                )
                return
            if not VoiceCommandRecognizer.backend_available():
                QMessageBox.warning(
                    self,
                    "Speech recognition unavailable",
                    "Vosk is not installed. Install requirements.txt "
                    "before running Voice training.",
                )
                return

        if not self._controller.device_manager.all_connected():
            answer = QMessageBox.question(
                self,
                "Devices not fully connected",
                "Not all study devices show as Connected. Start this run anyway?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        try:
            run, session = self._controller.workflow_manager.start_run(
                self._participant_code, self._step
            )
            live_rl = (
                environment == Environment.GRIDWORLD
                and modality in (Modality.KEYBOARD, Modality.VOICE)
            )
            condition = ExperimentCondition(
                study=Study.STUDY_2,
                environment=environment,
                feedback_timing=timing,
                modality=modality,
                rl_algorithm=(
                    "actor_critic_gridworld"
                    if live_rl
                    else "external_multimodal_training"
                ),
            )
            if live_rl:
                trial = self._controller.start_actor_critic_trial(
                    session.session_id,
                    condition,
                    practice=True,
                    use_maze_qinit=False,
                )
            else:
                trial = self._controller.start_tracked_trial(
                    session.session_id,
                    condition,
                    practice=True,
                )
            self._controller.workflow_manager.attach_trial(run.run_id, trial.trial_id)
        except Exception as exc:
            # If the workflow row was created but its practice Trial could not
            # start, close the workflow attempt as aborted rather than leaving
            # a permanently active Study 2 Training run.
            try:
                if 'run' in locals():
                    self._controller.workflow_manager.end_run(
                        run.run_id, completed=False, notes=f"Trial start failed: {exc}"
                    )
            except Exception:
                pass
            QMessageBox.critical(self, "Could not start run", str(exc))
            return

        self._notes_input.clear()
        self._active_run = run
        self._active_trial_id = trial.trial_id
        self.refresh()

    def _stop_run(self, completed: bool) -> None:
        if self._active_run is None:
            return

        active_trial = self._controller.active_trial
        if active_trial is not None and (
            active_trial.trial_id == self._active_trial_id
            or active_trial.session_id == self._active_run.session_id
        ):
            self._controller.stop_active_trial(completed=completed)

        self._controller.workflow_manager.end_run(
            self._active_run.run_id,
            completed=completed,
            notes=self._notes_input.toPlainText().strip(),
        )
        self._active_run = None
        self._active_trial_id = None
        self.refresh()
        if self._on_step_changed:
            self._on_step_changed()

    def _set_running_controls(self, running: bool) -> None:
        self._complete_btn.setEnabled(running)
        self._abort_btn.setEnabled(running)

    def _tick_elapsed(self) -> None:
        if self._active_run is None or self._active_run.started_at is None:
            return
        import time as _time

        seconds = int(_time.time() - self._active_run.started_at)
        self._elapsed_label.setText(f"Elapsed: {seconds // 60}m {seconds % 60}s")
