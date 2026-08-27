"""Study 1 training/familiarization panel.

The training window intentionally keeps the existing 2 x 4 practice matrix:
Requested/Anytime x Keyboard/Joystick/Voice/Eye Gaze. Experimental Study 1
uses a separate IRB-aligned panel (``study1_study_panel.py``).
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
    STUDY1_TRAINING_QUICK_PASS_NOTE,
    STUDY1_REQUIRED_CONDITION_COUNT,
    STUDY1_REQUIRED_MODALITIES,
    STUDY1_REQUIRED_TIMINGS,
)
from models.enums import (
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


class Study1StepPanel(QWidget):
    def __init__(
        self,
        controller: ApplicationController,
        step: WorkflowStep,
        on_step_changed,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if step != WorkflowStep.STUDY1_TRAINING:
            raise ValueError("Study1StepPanel is the Study 1 Training panel only")
        self._controller = controller
        self._step = step
        self._practice = True
        self._on_step_changed = on_step_changed

        self._participant_code: str | None = None
        self._active_run = None

        root = QVBoxLayout(self)

        title = QLabel(STEP_LABELS[step])
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        root.addWidget(title)

        note_text = (
            "Study 1 Training requires practice in all 8 combinations: Requested + "
            "Anytime feedback, each with Keyboard, Joystick, Voice, and Eye Gaze. "
            "Training progress is tracked separately from experimental Study 1 data."
        )
        note = QLabel(note_text)
        note.setWordWrap(True)
        note.setStyleSheet("color: #666; font-size: 11px;")
        root.addWidget(note)

        root.addWidget(self._build_condition_matrix())

        root.addWidget(self._build_config_box())
        root.addWidget(self._build_status_box())
        root.addWidget(self._build_history_box(), 1)

        self._controller.rl_manager.trial_started.connect(self._on_trial_started)
        self._controller.rl_manager.episode_finished.connect(self._on_episode_finished)
        self._controller.rl_manager.status_changed.connect(self._on_rl_status_changed)

    # -- Required condition matrix -----------------------------------------
    def _build_condition_matrix(self) -> QGroupBox:
        phase_name = "Training" if self._practice else "Study"
        box = QGroupBox(f"Required Study 1 {phase_name} Conditions")
        layout = QVBoxLayout(box)

        top = QHBoxLayout()
        self._matrix_summary_label = QLabel("0 / 8 conditions completed")
        self._matrix_summary_label.setStyleSheet("font-weight: bold;")
        top.addWidget(self._matrix_summary_label)
        top.addStretch()

        self._quick_pass_btn = QPushButton("Quick Pass All Tests")
        self._quick_pass_btn.setToolTip(
            "For participants already familiar with all feedback modalities and HoloLens use. "
            "Marks all 8 training requirements as passed without creating synthetic RL trials."
        )
        self._quick_pass_btn.clicked.connect(self._quick_pass_all_training)
        top.addWidget(self._quick_pass_btn)

        self._next_condition_btn = QPushButton("Select Next Incomplete")
        self._next_condition_btn.clicked.connect(self._select_next_incomplete)
        top.addWidget(self._next_condition_btn)
        layout.addLayout(top)

        self._condition_table = QTableWidget(
            len(STUDY1_REQUIRED_TIMINGS),
            len(STUDY1_REQUIRED_MODALITIES),
        )
        self._condition_table.setHorizontalHeaderLabels(
            [modality.value for modality in STUDY1_REQUIRED_MODALITIES]
        )
        self._condition_table.setVerticalHeaderLabels(
            [timing.value for timing in STUDY1_REQUIRED_TIMINGS]
        )
        self._condition_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self._condition_table.verticalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self._condition_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._condition_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._condition_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self._condition_table.cellClicked.connect(self._condition_cell_clicked)
        self._condition_table.setMinimumHeight(150)
        layout.addWidget(self._condition_table)

        legend = QLabel(
            "⬜ Not started    🔶 In progress    ✅ Completed    ⚠ Needs repeat   "
            "— click a cell to select that condition."
        )
        legend.setWordWrap(True)
        legend.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(legend)
        return box

    def _refresh_condition_matrix(self) -> None:
        if not hasattr(self, "_condition_table"):
            return

        if self._participant_code is None:
            self._matrix_summary_label.setText("Select a participant to view condition status.")
            for row in range(self._condition_table.rowCount()):
                for col in range(self._condition_table.columnCount()):
                    self._condition_table.setItem(row, col, QTableWidgetItem("⬜ Not Started"))
            self._next_condition_btn.setEnabled(False)
            self._quick_pass_btn.setEnabled(False)
            return

        quick_passed = self._controller.workflow_manager.study1_training_quick_passed(
            self._participant_code
        )
        summaries = self._controller.workflow_manager.study1_condition_statuses(
            self._participant_code,
            practice=self._practice,
        )
        by_pair = {
            (item.feedback_timing, item.modality): item
            for item in summaries
        }

        completed = 0
        for row, timing in enumerate(STUDY1_REQUIRED_TIMINGS):
            for col, modality in enumerate(STUDY1_REQUIRED_MODALITIES):
                summary = by_pair[(timing, modality)]
                if summary.status == "Completed":
                    completed += 1

                if quick_passed:
                    text = "✅ Passed\nQuick Pass"
                else:
                    text = f"{_STATUS_SYMBOL[summary.status]} {summary.status}"
                if not quick_passed and summary.completed_trials > 1:
                    text += f"\n{summary.completed_trials} completed runs"
                elif summary.total_trials > 1 and summary.status != "Completed":
                    text += f"\n{summary.total_trials} attempts"

                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if quick_passed:
                    item.setToolTip(
                        f"{timing.value} / {modality.value}\n"
                        "Training requirement satisfied by Quick Pass.\n"
                        "No synthetic RL trial was created for this condition."
                    )
                else:
                    item.setToolTip(
                        f"{timing.value} / {modality.value}\n"
                        f"Completed trials: {summary.completed_trials}\n"
                        f"Total attempts: {summary.total_trials}\n"
                        f"Last trial: {summary.last_trial_id or '--'}"
                    )
                self._condition_table.setItem(row, col, item)

        remaining = STUDY1_REQUIRED_CONDITION_COUNT - completed
        if completed == STUDY1_REQUIRED_CONDITION_COUNT:
            if quick_passed:
                self._matrix_summary_label.setText(
                    f"✅ {completed} / {STUDY1_REQUIRED_CONDITION_COUNT} conditions passed — "
                    "Training Quick Pass applied"
                )
            else:
                self._matrix_summary_label.setText(
                    f"✅ {completed} / {STUDY1_REQUIRED_CONDITION_COUNT} conditions completed — "
                    f"Study 1 {'Training' if self._practice else 'Study'} complete"
                )
            self._next_condition_btn.setEnabled(False)
            self._quick_pass_btn.setEnabled(False)
        else:
            self._matrix_summary_label.setText(
                f"{completed} / {STUDY1_REQUIRED_CONDITION_COUNT} conditions completed "
                f"({remaining} remaining)"
            )
            self._next_condition_btn.setEnabled(self._active_run is None)

        self._highlight_selected_condition()

    def _condition_cell_clicked(self, row: int, col: int) -> None:
        if row < 0 or col < 0:
            return
        self._set_condition_selection(
            STUDY1_REQUIRED_TIMINGS[row],
            STUDY1_REQUIRED_MODALITIES[col],
        )

    def _set_condition_selection(
        self, timing: FeedbackTiming, modality: Modality
    ) -> None:
        timing_index = self._timing_combo.findData(timing.name)
        modality_index = self._modality_combo.findData(modality.name)
        if timing_index >= 0:
            self._timing_combo.setCurrentIndex(timing_index)
        if modality_index >= 0:
            self._modality_combo.setCurrentIndex(modality_index)
        self._highlight_selected_condition()

    def _highlight_selected_condition(self) -> None:
        if not hasattr(self, "_condition_table"):
            return
        try:
            timing = FeedbackTiming[self._timing_combo.currentData()]
            modality = Modality[self._modality_combo.currentData()]
            row = STUDY1_REQUIRED_TIMINGS.index(timing)
            col = STUDY1_REQUIRED_MODALITIES.index(modality)
        except (KeyError, ValueError):
            return
        self._condition_table.setCurrentCell(row, col)

    def _select_next_incomplete(self) -> None:
        if self._participant_code is None:
            return
        next_condition = self._controller.workflow_manager.next_incomplete_study1_condition(
            self._participant_code,
            practice=self._practice,
        )
        if next_condition is None:
            return
        self._set_condition_selection(
            next_condition.feedback_timing,
            next_condition.modality,
        )

    # -- Config -------------------------------------------------------------
    def _build_config_box(self) -> QGroupBox:
        box = QGroupBox("Configuration")
        form = QFormLayout(box)

        self._timing_combo = QComboBox()
        for timing in STUDY1_REQUIRED_TIMINGS:
            self._timing_combo.addItem(timing.value, timing.name)
        self._timing_combo.currentIndexChanged.connect(self._highlight_selected_condition)
        form.addRow("Feedback timing:", self._timing_combo)

        self._modality_combo = QComboBox()
        for modality in STUDY1_REQUIRED_MODALITIES:
            self._modality_combo.addItem(modality.value, modality.name)
        self._modality_combo.currentIndexChanged.connect(self._highlight_selected_condition)
        form.addRow("Feedback modality:", self._modality_combo)

        self._seed_spin = QSpinBox()
        self._seed_spin.setRange(0, 2_147_483_647)
        self._seed_spin.setValue(int(self._controller.config.study_raw.get("random_seed", 42)))
        form.addRow("Random seed:", self._seed_spin)

        self._warm_start = QCheckBox("Use maze-informed Actor/Critic warm start")
        form.addRow("Warm start:", self._warm_start)

        return box

    # -- Status / controls --------------------------------------------------
    def _build_status_box(self) -> QGroupBox:
        box = QGroupBox("Run")
        layout = QVBoxLayout(box)

        self._status_label = QLabel("No run in progress.")
        layout.addWidget(self._status_label)

        info_row = QHBoxLayout()
        self._episode_label = QLabel("Episode: --")
        self._reward_label = QLabel("Last reward: --")
        info_row.addWidget(self._episode_label)
        info_row.addWidget(self._reward_label)
        info_row.addStretch()
        layout.addLayout(info_row)

        buttons = QHBoxLayout()
        label = "Start Selected Training Condition" if self._practice else "Start Selected Condition"
        self._start_btn = QPushButton(label)
        self._start_btn.clicked.connect(self._start_run)

        self._pause_btn = QPushButton("Pause")
        self._pause_btn.clicked.connect(self._controller.pause_active_trial)

        self._resume_btn = QPushButton("Resume")
        self._resume_btn.clicked.connect(self._controller.resume_active_trial)

        self._stop_btn = QPushButton("Stop && Mark Complete")
        self._stop_btn.clicked.connect(lambda: self._stop_run(completed=True))

        self._abort_btn = QPushButton("Abort Run")
        self._abort_btn.clicked.connect(lambda: self._stop_run(completed=False))

        for btn in (
            self._start_btn,
            self._pause_btn,
            self._resume_btn,
            self._stop_btn,
            self._abort_btn,
        ):
            buttons.addWidget(btn)
        layout.addLayout(buttons)

        self._set_running_controls(False)
        return box

    def _build_history_box(self) -> QGroupBox:
        box = QGroupBox("Run History")
        layout = QVBoxLayout(box)
        self._history_table = QTableWidget(0, 7)
        self._history_table.setHorizontalHeaderLabels(
            ["Run", "Timing", "Modality", "Status", "Started", "Ended", "Duration"]
        )
        self._history_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self._history_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self._history_table)
        return box

    # -- Public API ---------------------------------------------------------
    def set_participant(self, participant_code: str | None) -> None:
        participant_changed = participant_code != self._participant_code
        self._participant_code = participant_code
        self.refresh()
        if participant_changed and participant_code is not None:
            self._select_next_incomplete()

    def refresh(self) -> None:
        self._refresh_condition_matrix()

        if self._participant_code is None:
            self._status_label.setText("Select or register a participant first.")
            self._set_running_controls(False)
            self._start_btn.setEnabled(False)
            self._quick_pass_btn.setEnabled(False)
            self._history_table.setRowCount(0)
            return

        summary = self._controller.workflow_manager.step_status(
            self._participant_code, self._step
        )
        self._active_run = summary.active_run

        blocking_run = self._controller.workflow_manager.has_active_run(
            self._participant_code
        )
        self._quick_pass_btn.setEnabled(
            blocking_run is None
            and summary.completed_count < STUDY1_REQUIRED_CONDITION_COUNT
        )

        if self._active_run is not None:
            self._status_label.setText(f"In progress: {self._active_run.run_id}")
            self._set_running_controls(True)
            self._start_btn.setEnabled(False)
        else:
            self._set_running_controls(False)
            self._start_btn.setEnabled(blocking_run is None)

            if blocking_run is not None:
                self._status_label.setText(
                    f"Another run ({blocking_run.run_id}) is in progress for this participant. "
                    "Finish or abort it first."
                )
            else:
                completed = summary.completed_count
                phase_name = "Training" if self._practice else "Study"

                # Experimental Study 1 should only start after the participant
                # has practiced all eight matching conditions.
                training_complete = True
                training_completed = STUDY1_REQUIRED_CONDITION_COUNT
                if not self._practice:
                    training_summary = self._controller.workflow_manager.step_status(
                        self._participant_code,
                        WorkflowStep.STUDY1_TRAINING,
                    )
                    training_completed = training_summary.completed_count
                    training_complete = (
                        training_completed == STUDY1_REQUIRED_CONDITION_COUNT
                    )

                if completed == STUDY1_REQUIRED_CONDITION_COUNT:
                    self._status_label.setText(
                        f"All 8 required Study 1 {phase_name.lower()} conditions are completed."
                    )
                elif not training_complete:
                    self._status_label.setText(
                        f"Complete Study 1 Training first: {training_completed}/"
                        f"{STUDY1_REQUIRED_CONDITION_COUNT} training conditions completed."
                    )
                    self._start_btn.setEnabled(False)
                else:
                    next_condition = (
                        self._controller.workflow_manager.next_incomplete_study1_condition(
                            self._participant_code,
                            practice=self._practice,
                        )
                    )
                    next_text = ""
                    if next_condition is not None:
                        next_text = (
                            f" Next incomplete: {next_condition.feedback_timing.value} / "
                            f"{next_condition.modality.value}."
                        )
                    self._status_label.setText(
                        f"Study 1 {phase_name.lower()} progress: "
                        f"{completed}/{STUDY1_REQUIRED_CONDITION_COUNT} "
                        f"required conditions completed.{next_text}"
                    )

        self._refresh_history()
        self._refresh_condition_matrix()

    def _refresh_history(self) -> None:
        runs = self._controller.workflow_manager.list_runs(
            self._participant_code, self._step
        )
        self._history_table.setRowCount(len(runs))
        for row, run in enumerate(reversed(runs)):
            timing = "--"
            modality = "--"
            status_text = run.status.value
            if run.notes == STUDY1_TRAINING_QUICK_PASS_NOTE:
                timing = "All"
                modality = "All"
                status_text = f"{run.status.value} — Quick Pass"
            if run.trial_id:
                trial = self._controller.trial_manager.get_trial(run.trial_id)
                if trial is not None:
                    timing = trial.condition.feedback_timing.value
                    modality = trial.condition.modality.value

            self._history_table.setItem(row, 0, QTableWidgetItem(run.run_id))
            self._history_table.setItem(row, 1, QTableWidgetItem(timing))
            self._history_table.setItem(row, 2, QTableWidgetItem(modality))
            self._history_table.setItem(row, 3, QTableWidgetItem(status_text))
            self._history_table.setItem(row, 4, QTableWidgetItem(_fmt_time(run.started_at)))
            self._history_table.setItem(row, 5, QTableWidgetItem(_fmt_time(run.ended_at)))
            self._history_table.setItem(
                row, 6, QTableWidgetItem(_fmt_duration(run.started_at, run.ended_at))
            )

    # -- Actions ------------------------------------------------------------
    def _quick_pass_all_training(self) -> None:
        if self._participant_code is None:
            return

        summary = self._controller.workflow_manager.step_status(
            self._participant_code, WorkflowStep.STUDY1_TRAINING
        )
        if summary.completed_count == STUDY1_REQUIRED_CONDITION_COUNT:
            QMessageBox.information(
                self,
                "Training already complete",
                "All Study 1 training requirements are already satisfied.",
            )
            return

        blocking = self._controller.workflow_manager.has_active_run(self._participant_code)
        if blocking is not None:
            QMessageBox.warning(
                self,
                "Run in progress",
                f"Finish or abort {blocking.run_id} before using Quick Pass.",
            )
            return

        answer = QMessageBox.question(
            self,
            "Quick Pass all Study 1 training tests?",
            "Use this only when the participant is already familiar with the feedback "
            "modalities and HoloLens eye-gaze interaction.\n\n"
            "This will mark all 8 Study 1 training conditions as PASSED and allow the "
            "participant to proceed to the study. No synthetic RL training trials will "
            "be created; one Quick Pass entry will be kept in workflow history.\n\n"
            "Apply Quick Pass now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        try:
            self._controller.workflow_manager.quick_pass_study1_training(
                self._participant_code
            )
        except Exception as exc:
            QMessageBox.critical(self, "Could not Quick Pass training", str(exc))
            return


        self.refresh()
        if self._on_step_changed:
            self._on_step_changed()

    def _start_run(self) -> None:
        if self._participant_code is None:
            return

        timing = FeedbackTiming[self._timing_combo.currentData()]
        modality = Modality[self._modality_combo.currentData()]

        if not self._practice:
            training_summary = self._controller.workflow_manager.step_status(
                self._participant_code,
                WorkflowStep.STUDY1_TRAINING,
            )
            if training_summary.completed_count < STUDY1_REQUIRED_CONDITION_COUNT:
                QMessageBox.warning(
                    self,
                    "Study 1 Training incomplete",
                    f"Complete all 8 Study 1 Training conditions before starting "
                    f"experimental Study 1. Current training progress: "
                    f"{training_summary.completed_count}/{STUDY1_REQUIRED_CONDITION_COUNT}.",
                )
                return

        condition_status = self._controller.workflow_manager.study1_condition_status(
            self._participant_code,
            timing,
            modality,
            practice=self._practice,
        )
        if condition_status.status == "Completed":
            phase_name = "training" if self._practice else "study"
            answer = QMessageBox.question(
                self,
                "Condition already completed",
                f"{timing.value} / {modality.value} is already completed for Study 1 "
                f"{phase_name}.\n\nStart a repeat run anyway?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        if modality == Modality.EYE_GAZE:
            ok, message = self._controller.device_manager.check_hololens()
            eye = self._controller.device_manager.hololens_latest_eye_data()
            calibrated = bool(eye.get("calibration_valid", False))
            if not ok or not calibrated:
                QMessageBox.warning(
                    self,
                    "HoloLens eye gaze unavailable",
                    "Eye Gaze training needs a connected HoloLens 2 with fresh "
                    "Extended Eye Tracking data and valid eye calibration.\n\n"
                    f"{message}\n\nCalibrate eye tracking on the headset, then try again.",
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
        except Exception as exc:
            QMessageBox.critical(self, "Could not start run", str(exc))
            return

        condition = ExperimentCondition(
            study=Study.STUDY_1,
            environment=Environment.GRIDWORLD,
            feedback_timing=timing,
            modality=modality,
            rl_algorithm="actor_critic_gridworld",
            random_seed=self._seed_spin.value(),
        )

        try:
            trial = self._controller.start_actor_critic_trial(
                session_id=session.session_id,
                condition=condition,
                practice=self._practice,
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
        self._active_run = run
        self.refresh()

    def _stop_run(self, completed: bool) -> None:
        if self._controller.active_trial is None or self._active_run is None:
            return
        self._controller.stop_active_trial(completed=completed)
        self._controller.workflow_manager.end_run(
            self._active_run.run_id, completed=completed
        )
        self._active_run = None
        self.refresh()
        if completed:
            self._select_next_incomplete()
        if self._on_step_changed:
            self._on_step_changed()

    def _set_running_controls(self, running: bool) -> None:
        self._pause_btn.setEnabled(running)
        self._resume_btn.setEnabled(running)
        self._stop_btn.setEnabled(running)
        self._abort_btn.setEnabled(running)
        if hasattr(self, "_next_condition_btn"):
            self._next_condition_btn.setEnabled(not running)

    # -- RL signal handlers -------------------------------------------------
    def _is_mine(self, trial) -> bool:
        return (
            self._active_run is not None
            and trial is not None
            and trial.trial_id == self._active_run.trial_id
        )

    def _on_trial_started(self, trial) -> None:
        if self._is_mine(trial):
            self._set_running_controls(True)
            self._refresh_condition_matrix()

    def _on_episode_finished(self, payload: dict) -> None:
        trial = self._controller.active_trial
        if not self._is_mine(trial):
            return
        self._episode_label.setText(f"Episode: {payload['episode']}")
        self._reward_label.setText(f"Last reward: {payload['total_reward']:.2f}")

    def _on_rl_status_changed(self, status: str) -> None:
        trial = self._controller.active_trial
        if self._active_run is None:
            return
        if trial is not None and self._is_mine(trial):
            self._status_label.setText(
                f"In progress: {self._active_run.run_id} — {status}"
            )
