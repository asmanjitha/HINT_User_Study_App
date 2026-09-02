"""Study 2 panel: HOW should a human provide Gridworld feedback?

Study 2 uses only the Gridworld environment.  The experimenter chooses one
feedback timing (System Requested or Anytime) and then collects whichever one
or two modalities are desired for that participant from Keyboard, Joystick,
and Voice.  Completing every modality is intentionally *not* required; a
researcher-facing Finish Study 2 button creates an explicit completion marker.
"""

from __future__ import annotations

import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.application_controller import ApplicationController
from core.workflow_manager import (
    STEP_LABELS,
    STUDY2_REQUIRED_MODALITIES,
    condition_status_is_complete,
)
from devices.voice_recognizer import VoiceCommandRecognizer
from models.enums import (
    CollectionRunStatus,
    Environment,
    FeedbackTiming,
    Modality,
    Study,
    WorkflowStep,
)
from models.trial import ExperimentCondition

_STATUS_SYMBOL = {
    "Not Started": "⬜",
    "In Progress": "🔶",
    "Completed": "✅",
    "Manually Completed": "✅",
    "Needs Repeat": "⚠",
}


def _fmt_time(ts: float | None) -> str:
    if not ts:
        return "--"
    return datetime.datetime.fromtimestamp(ts).strftime("%H:%M:%S")


def _fmt_duration(started: float | None, ended: float | None) -> str:
    if not started or not ended:
        return "--"
    seconds = int(ended - started)
    return f"{seconds // 60}m {seconds % 60}s"


class Study2StudyPanel(QWidget):
    def __init__(
        self,
        controller: ApplicationController,
        on_step_changed,
        on_study_finished=None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._step = WorkflowStep.STUDY2_STUDY
        self._on_step_changed = on_step_changed
        self._on_study_finished = on_study_finished
        self._participant_code: str | None = None
        self._active_run = None
        self._active_live_rl = False

        root = QVBoxLayout(self)
        title = QLabel(STEP_LABELS[self._step])
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        root.addWidget(title)

        note = QLabel(
            "Study 2 investigates HOW feedback should be provided. It uses only the "
            "2D Gridworld. Choose System Requested or Anytime feedback, then run the "
            "participant with the Keyboard, Joystick, and/or Voice modality you want "
            "to evaluate. You do not need to complete every modality. Use Finish "
            "Study 2 when you are ready to continue, even if no modality was collected."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #666; font-size: 11px;")
        root.addWidget(note)

        root.addWidget(self._build_matrix())
        root.addWidget(self._build_config())
        root.addWidget(self._build_status())
        root.addWidget(self._build_history(), 1)

        self._controller.rl_manager.trial_started.connect(self._on_trial_started)
        self._controller.rl_manager.episode_finished.connect(self._on_episode_finished)
        self._controller.rl_manager.status_changed.connect(self._on_rl_status_changed)

    def _build_matrix(self) -> QGroupBox:
        box = QGroupBox("Available Study 2 feedback modalities")
        layout = QVBoxLayout(box)

        top = QHBoxLayout()
        self._summary_label = QLabel("0 modality runs completed")
        self._summary_label.setStyleSheet("font-weight: bold;")
        top.addWidget(self._summary_label)
        top.addStretch()
        self._mark_item_btn = QPushButton("Mark Selected Modality Complete…")
        self._mark_item_btn.clicked.connect(self._mark_selected_complete)
        top.addWidget(self._mark_item_btn)
        self._next_btn = QPushButton("Select Next Uncollected")
        self._next_btn.clicked.connect(self._select_next)
        top.addWidget(self._next_btn)
        layout.addLayout(top)

        self._table = QTableWidget(1, len(STUDY2_REQUIRED_MODALITIES))
        self._table.setHorizontalHeaderLabels([m.value for m in STUDY2_REQUIRED_MODALITIES])
        self._table.setVerticalHeaderLabels(["2D Gridworld"])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self._table.cellClicked.connect(self._cell_clicked)
        self._table.setMinimumHeight(100)
        layout.addWidget(self._table)
        return box

    def _build_config(self) -> QGroupBox:
        box = QGroupBox("Selected Study 2 configuration")
        form = QFormLayout(box)

        fixed_env = QLabel(Environment.GRIDWORLD.value)
        fixed_env.setStyleSheet("font-weight: bold;")
        form.addRow("Environment:", fixed_env)

        self._timing_combo = QComboBox()
        for timing in (FeedbackTiming.REQUESTED, FeedbackTiming.ANYTIME):
            self._timing_combo.addItem(timing.value, timing.name)
        self._timing_combo.currentIndexChanged.connect(self._on_selection_changed)
        form.addRow("Feedback timing:", self._timing_combo)

        self._modality_combo = QComboBox()
        for modality in STUDY2_REQUIRED_MODALITIES:
            self._modality_combo.addItem(modality.value, modality.name)
        self._modality_combo.currentIndexChanged.connect(self._on_selection_changed)
        form.addRow("Feedback modality:", self._modality_combo)

        self._seed_spin = QSpinBox()
        self._seed_spin.setRange(0, 2_147_483_647)
        self._seed_spin.setValue(int(self._controller.config.study_raw.get("random_seed", 42)))
        form.addRow("Random seed:", self._seed_spin)

        self._warm_start = QCheckBox("Use maze-informed Actor/Critic warm start")
        form.addRow("Warm start:", self._warm_start)

        self._execution_label = QLabel("")
        self._execution_label.setWordWrap(True)
        self._execution_label.setStyleSheet("color: #555;")
        form.addRow("Execution:", self._execution_label)
        return box

    def _build_status(self) -> QGroupBox:
        box = QGroupBox("Run and Study 2 completion")
        layout = QVBoxLayout(box)
        self._status_label = QLabel("No run in progress.")
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

        self._data_folder_label = QLabel("Next data folder: --")
        self._data_folder_label.setWordWrap(True)
        self._data_folder_label.setStyleSheet("color: #555; font-family: monospace;")
        layout.addWidget(self._data_folder_label)

        metrics = QHBoxLayout()
        self._episode_label = QLabel("Episode: --")
        self._reward_label = QLabel("Last reward: --")
        metrics.addWidget(self._episode_label)
        metrics.addWidget(self._reward_label)
        metrics.addStretch()
        layout.addLayout(metrics)

        buttons = QHBoxLayout()
        self._start_btn = QPushButton("Start Selected Modality")
        self._start_btn.clicked.connect(self._start_run)
        self._pause_btn = QPushButton("Pause")
        self._pause_btn.clicked.connect(self._controller.pause_active_trial)
        self._resume_btn = QPushButton("Resume")
        self._resume_btn.clicked.connect(self._controller.resume_active_trial)
        self._complete_btn = QPushButton("Stop && Mark Valid")
        self._complete_btn.clicked.connect(lambda: self._finish_run(CollectionRunStatus.VALID))
        self._invalid_btn = QPushButton("Mark Invalid / Repeat")
        self._invalid_btn.clicked.connect(self._mark_invalid_and_repeat)
        self._abort_btn = QPushButton("Abort Run")
        self._abort_btn.clicked.connect(lambda: self._finish_run(CollectionRunStatus.ABORTED))
        for button in (
            self._start_btn,
            self._pause_btn,
            self._resume_btn,
            self._complete_btn,
            self._invalid_btn,
            self._abort_btn,
        ):
            buttons.addWidget(button)
        layout.addLayout(buttons)

        finish_row = QHBoxLayout()
        finish_row.addStretch()
        self._finish_study_btn = QPushButton("Finish Study 2 && Continue")
        self._finish_study_btn.setToolTip(
            "Use after collecting the modality run(s) selected for this participant."
        )
        self._finish_study_btn.clicked.connect(self._finish_study2)
        finish_row.addWidget(self._finish_study_btn)
        layout.addLayout(finish_row)

        self._set_running_controls(False)
        return box

    def _build_history(self) -> QGroupBox:
        box = QGroupBox("Study 2 Run History")
        layout = QVBoxLayout(box)
        self._history = QTableWidget(0, 8)
        self._history.setHorizontalHeaderLabels(
            [
                "Condition", "Attempt", "Modality", "Timing", "Result",
                "Started", "Ended", "Duration",
            ]
        )
        self._history.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._history.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self._history)
        return box

    # ------------------------------------------------------------------
    def set_participant(self, participant_code: str | None) -> None:
        changed = participant_code != self._participant_code
        self._participant_code = participant_code
        self.refresh()
        if changed and participant_code:
            self._select_next()

    def refresh(self) -> None:
        self._refresh_matrix()
        self._refresh_history()
        self._update_execution_hint()

        if self._participant_code is None:
            self._status_label.setText("Select or register a participant first.")
            self._start_btn.setEnabled(False)
            self._finish_study_btn.setEnabled(False)
            self._set_running_controls(False)
            return

        summary = self._controller.workflow_manager.step_status(self._participant_code, self._step)
        self._active_run = summary.active_run
        blocking = self._controller.workflow_manager.has_active_run(self._participant_code)
        finished = self._controller.workflow_manager.study2_finished(self._participant_code)
        statuses = self._controller.workflow_manager.study2_condition_statuses(self._participant_code)
        completed = sum(1 for item in statuses if condition_status_is_complete(item.status))

        if self._active_run is not None:
            trial = (
                self._controller.trial_manager.get_trial(self._active_run.trial_id)
                if self._active_run.trial_id else None
            )
            label = trial.readable_run_label if trial is not None else self._active_run.run_id
            self._status_label.setText(f"In progress: {label}")
            if trial is not None and trial.trial_path is not None:
                try:
                    rel = trial.trial_path.relative_to(self._controller.config.data_dir)
                except ValueError:
                    rel = trial.trial_path
                self._data_folder_label.setText(f"Current data folder: {rel}")
            self._start_btn.setEnabled(False)
            self._finish_study_btn.setEnabled(False)
            self._set_running_controls(True)
            return

        self._active_live_rl = False
        self._set_running_controls(False)

        if blocking is not None:
            self._status_label.setText(
                f"Another run ({blocking.run_id}) is in progress. Finish or abort it first."
            )
            self._start_btn.setEnabled(False)
            self._finish_study_btn.setEnabled(False)
        elif finished:
            self._status_label.setText(
                f"Study 2 has been marked finished by the experimenter. "
                f"{completed} modality condition(s) are complete. Continue to Agent Observation."
            )
            self._start_btn.setEnabled(False)
            self._finish_study_btn.setEnabled(False)
        else:
            self._status_label.setText(
                f"Study 2 collection: {completed} modality condition(s) completed. "
                "Collect the one or two modalities selected for this participant, then press Finish Study 2."
            )
            self._start_btn.setEnabled(True)
            self._finish_study_btn.setEnabled(True)

    def _refresh_matrix(self) -> None:
        if self._participant_code is None:
            self._summary_label.setText("Select a participant to view modality status.")
            for col in range(len(STUDY2_REQUIRED_MODALITIES)):
                self._table.setItem(0, col, QTableWidgetItem("⬜ Not Started"))
            self._next_btn.setEnabled(False)
            return

        statuses = self._controller.workflow_manager.study2_condition_statuses(self._participant_code)
        completed = 0
        for col, item in enumerate(statuses):
            if condition_status_is_complete(item.status):
                completed += 1
            text = f"{_STATUS_SYMBOL[item.status]} {item.status}"
            if item.last_feedback_timing is not None:
                text += f"\nLast: {item.last_feedback_timing.value}"
            cell = QTableWidgetItem(text)
            cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(0, col, cell)

        finished = self._controller.workflow_manager.study2_finished(self._participant_code)
        finish_text = " — Study 2 finished" if finished else ""
        self._summary_label.setText(f"{completed} modality condition(s) completed{finish_text}")
        self._mark_item_btn.setEnabled(self._participant_code is not None and self._active_run is None and not finished)
        self._next_btn.setEnabled(self._active_run is None and not finished)
        self._highlight_selected()

    def _refresh_history(self) -> None:
        if self._participant_code is None:
            self._history.setRowCount(0)
            return
        trials = self._controller.trial_manager.list_trials(
            self._participant_code, study=Study.STUDY_2, practice=False
        )
        self._history.setRowCount(len(trials))
        for row, trial in enumerate(reversed(trials)):
            result = trial.collection_status.value
            if result == CollectionRunStatus.PENDING.value:
                result = trial.status.value
            vals = [
                trial.condition_code or "--",
                trial.run_code or "--",
                trial.condition.modality.value,
                trial.condition.feedback_timing.value,
                result,
                _fmt_time(trial.started_at),
                _fmt_time(trial.ended_at),
                _fmt_duration(trial.started_at, trial.ended_at),
            ]
            for col, value in enumerate(vals):
                self._history.setItem(row, col, QTableWidgetItem(value))

    # ------------------------------------------------------------------
    def _cell_clicked(self, _row: int, col: int) -> None:
        if 0 <= col < len(STUDY2_REQUIRED_MODALITIES):
            idx = self._modality_combo.findData(STUDY2_REQUIRED_MODALITIES[col].name)
            if idx >= 0:
                self._modality_combo.setCurrentIndex(idx)

    def _highlight_selected(self) -> None:
        try:
            modality = Modality[self._modality_combo.currentData()]
            col = STUDY2_REQUIRED_MODALITIES.index(modality)
            self._table.setCurrentCell(0, col)
        except (KeyError, ValueError, TypeError):
            pass

    def _select_next(self) -> None:
        if self._participant_code is None:
            return
        item = self._controller.workflow_manager.next_incomplete_study2_condition(
            self._participant_code
        )
        if item is None:
            return
        idx = self._modality_combo.findData(item.modality.name)
        if idx >= 0:
            self._modality_combo.setCurrentIndex(idx)

    def _on_selection_changed(self) -> None:
        self._highlight_selected()
        self._update_execution_hint()

    def _update_execution_hint(self) -> None:
        if not hasattr(self, "_execution_label"):
            return
        try:
            modality = Modality[self._modality_combo.currentData()]
            timing = FeedbackTiming[self._timing_combo.currentData()]
        except (KeyError, TypeError):
            return

        if modality == Modality.KEYBOARD:
            detail = (
                "Keyboard controls the corrective direction."
                if timing == FeedbackTiming.REQUESTED
                else "Press SPACE to pause, select a recent state, then use the keyboard direction keys."
            )
        elif modality == Modality.VOICE:
            detail = (
                "Say UP, DOWN, LEFT, or RIGHT when feedback is requested."
                if timing == FeedbackTiming.REQUESTED
                else 'Say "STOP" to pause, speak the state-box number, then say the corrective direction.'
            )
        else:
            detail = (
                "Tilt the connected joystick in the corrective direction when feedback is requested."
                if timing == FeedbackTiming.REQUESTED
                else "Press joystick button 1 to pause, use LEFT/RIGHT to choose a recent state, "
                "press button 1 to confirm it, then tilt the stick in the corrective direction."
            )
        self._execution_label.setText(f"Live Actor-Critic Gridworld. {detail}")
        self._update_data_folder_preview()

    def _condition_from_selection(self) -> ExperimentCondition:
        modality = Modality[self._modality_combo.currentData()]
        timing = FeedbackTiming[self._timing_combo.currentData()]
        return ExperimentCondition(
            study=Study.STUDY_2,
            environment=Environment.GRIDWORLD,
            feedback_timing=timing,
            modality=modality,
            rl_algorithm="actor_critic_gridworld",
            random_seed=self._seed_spin.value(),
        )

    def _update_data_folder_preview(self) -> None:
        if not hasattr(self, "_data_folder_label"):
            return
        if self._participant_code is None:
            self._data_folder_label.setText("Next data folder: --")
            return
        try:
            session_id = self._controller.session_manager.preview_session_id(self._participant_code)
            preview = self._controller.trial_manager.preview_storage(
                participant_code=self._participant_code,
                session_id=session_id,
                condition=self._condition_from_selection(),
                practice=False,
            )
            self._data_folder_label.setText(f"Next data folder: {preview['relative_dir']}")
        except Exception:
            self._data_folder_label.setText("Next data folder: unavailable")

    # ------------------------------------------------------------------
    def _validate_modality_device(self, modality: Modality) -> bool:
        if modality == Modality.VOICE:
            ok, message = self._controller.device_manager.check_microphone()
            if not ok:
                QMessageBox.warning(
                    self,
                    "Voice microphone unavailable",
                    "The Voice condition needs a connected microphone receiving data.\n\n"
                    f"{message}\n\nOpen Devices, connect/check the microphone, then try again.",
                )
                return False
            if not VoiceCommandRecognizer.backend_available():
                QMessageBox.warning(
                    self,
                    "Speech recognition unavailable",
                    "The Vosk Python package is not installed. Install requirements.txt "
                    "before running Voice feedback.",
                )
                return False
        elif modality == Modality.JOYSTICK:
            ok, message = self._controller.device_manager.check_joystick()
            if not ok:
                QMessageBox.warning(
                    self,
                    "Joystick unavailable",
                    "The Joystick condition needs a connected joystick/gamepad.\n\n"
                    f"{message}\n\nOpen Devices, select and connect the joystick, then try again.",
                )
                return False
        return True

    def _start_run(self) -> None:
        if self._participant_code is None:
            return

        if self._controller.workflow_manager.study2_finished(self._participant_code):
            QMessageBox.information(
                self, "Study 2 already finished", "Study 2 is already marked finished for this participant."
            )
            return

        condition = self._condition_from_selection()
        modality = condition.modality
        existing = self._controller.workflow_manager.study2_condition_status(
            self._participant_code, modality
        )
        if condition_status_is_complete(existing.status):
            answer = QMessageBox.question(
                self,
                "Modality already completed",
                f"{modality.value} is already complete (valid run or manual override). "
                "Start a repeat run anyway?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        if not self._validate_modality_device(modality):
            return

        if not self._controller.device_manager.shimmer_stream_healthy():
            answer = QMessageBox.question(
                self,
                "Shimmer is not receiving live data",
                "No fresh Shimmer GSR/PPG samples are reaching the GUI. Starting now "
                "means no trial-specific physiological CSV will be created.\n\n"
                "Return to Devices and reconnect/check live data.\n\n"
                "Start this Study 2 run without Shimmer recording anyway?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        try:
            run, session = self._controller.workflow_manager.start_run(
                self._participant_code, self._step
            )
        except Exception as exc:
            QMessageBox.critical(self, "Could not start run", str(exc))
            return

        self._active_live_rl = True
        try:
            trial = self._controller.start_actor_critic_trial(
                session_id=session.session_id,
                condition=condition,
                practice=False,
                use_maze_qinit=self._warm_start.isChecked(),
            )
        except Exception as exc:
            self._controller.workflow_manager.end_run(
                run.run_id, completed=False, notes=f"Failed to start: {exc}"
            )
            QMessageBox.critical(self, "Could not start trial", str(exc))
            self.refresh()
            return

        self._controller.workflow_manager.attach_trial(run.run_id, trial.trial_id)
        self._active_run = self._controller.workflow_manager.get_run(run.run_id)
        self.refresh()

    def _finish_study2(self) -> None:
        if self._participant_code is None:
            return
        if self._controller.workflow_manager.has_active_run(self._participant_code) is not None:
            QMessageBox.warning(self, "Run in progress", "Finish or abort the current run first.")
            return

        statuses = self._controller.workflow_manager.study2_condition_statuses(self._participant_code)
        completed = [item.modality.value for item in statuses if condition_status_is_complete(item.status)]

        answer = QMessageBox.question(
            self,
            "Finish Study 2?",
            "Mark Study 2 finished for this participant and continue to the Agent Observation phase?\n\n"
            f"Completed modality condition(s): {', '.join(completed) if completed else 'None'}\n\n"
            "The remaining modalities are intentionally not required.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self._controller.workflow_manager.finish_study2(self._participant_code)
        except Exception as exc:
            QMessageBox.critical(self, "Could not finish Study 2", str(exc))
            return
        self.refresh()
        if self._on_step_changed:
            self._on_step_changed()
        if self._on_study_finished:
            self._on_study_finished()

    def _mark_selected_complete(self) -> None:
        if self._participant_code is None or self._active_run is not None:
            return
        modality = Modality[self._modality_combo.currentData()]
        status = self._controller.workflow_manager.study2_condition_status(
            self._participant_code, modality
        )
        if condition_status_is_complete(status.status):
            QMessageBox.information(self, "Already complete", f"'{modality.value}' is already complete.")
            return
        reason, ok = QInputDialog.getText(
            self,
            "Manual completion reason",
            f"Reason for marking '{modality.value}' complete:",
        )
        if not ok:
            return
        try:
            self._controller.workflow_manager.mark_completion_override(
                self._participant_code,
                self._step,
                item_key=modality.name,
                reason=reason,
            )
        except Exception as exc:
            QMessageBox.warning(self, "Could not mark modality complete", str(exc))
            return
        self.refresh()
        self._select_next()
        if self._on_step_changed:
            self._on_step_changed()

    def _mark_invalid_and_repeat(self) -> None:
        reasons = [
            "Participant mistake / misunderstood instructions",
            "Equipment failure",
            "Shimmer disconnected",
            "Input device problem",
            "Experimenter error",
            "Software error",
            "Other",
        ]
        reason, ok = QInputDialog.getItem(
            self, "Mark run invalid", "Reason for repeating this condition:", reasons, 0, False
        )
        if not ok:
            return
        if reason == "Other":
            reason, ok = QInputDialog.getText(self, "Invalid run reason", "Enter reason:")
            if not ok or not reason.strip():
                return
        self._finish_run(CollectionRunStatus.INVALID, reason.strip())

    def _finish_run(self, outcome: CollectionRunStatus, reason: str = "") -> None:
        if self._active_run is None:
            return
        completed = outcome != CollectionRunStatus.ABORTED
        if self._controller.active_trial is not None:
            self._controller.stop_active_trial(
                completed=completed,
                collection_status=outcome,
                repeat_reason=reason,
            )
        self._controller.workflow_manager.end_run(
            self._active_run.run_id,
            completed=completed,
            notes=reason,
            outcome=outcome,
        )
        self._active_run = None
        self._active_live_rl = False
        self.refresh()
        if outcome == CollectionRunStatus.VALID:
            self._select_next()
        else:
            self._update_data_folder_preview()
        if self._on_step_changed:
            self._on_step_changed()

    def _set_running_controls(self, running: bool) -> None:
        live = running and self._active_live_rl
        self._pause_btn.setEnabled(live)
        self._resume_btn.setEnabled(live)
        self._complete_btn.setEnabled(running)
        self._invalid_btn.setEnabled(running)
        self._abort_btn.setEnabled(running)
        self._next_btn.setEnabled(not running)

    def _is_mine(self, trial) -> bool:
        return (
            self._active_run is not None
            and trial is not None
            and trial.trial_id == self._active_run.trial_id
        )

    def _on_trial_started(self, trial) -> None:
        if self._is_mine(trial):
            self._active_live_rl = True
            self._set_running_controls(True)

    def _on_episode_finished(self, payload: dict) -> None:
        trial = self._controller.active_trial
        if self._is_mine(trial):
            self._episode_label.setText(f"Episode: {payload['episode']}")
            self._reward_label.setText(f"Last reward: {payload['total_reward']:.2f}")

    def _on_rl_status_changed(self, status: str) -> None:
        trial = self._controller.active_trial
        if self._is_mine(trial):
            label = trial.readable_run_label if trial is not None else self._active_run.run_id
            self._status_label.setText(f"In progress: {label} — {status}")
