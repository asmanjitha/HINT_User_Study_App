"""Researcher-facing Study 1 experimental panel aligned with the IRB flow.

Study 1 investigates WHEN a human should intervene. It uses Keyboard only
and requires Requested/Anytime conditions in both Gridworld and the continuous
action-space room environment.

The existing Actor-Critic Gridworld integration is launched for Keyboard
Gridworld trials.  Conditions that currently depend on an external/not-yet-
integrated adapter are still represented as real tracked Trials so progress,
metadata, timestamps, and folders remain correct without falsely treating a
keyboard click as another modality.
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
    QLineEdit,
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
    EXPLICIT_STUDY1_MODALITIES,
    STEP_LABELS,
    STUDY1_STUDY_REQUIRED_CONDITIONS,
    STUDY1_STUDY_REQUIRED_CONDITION_COUNT,
    condition_status_is_complete,
)
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


class Study1StudyPanel(QWidget):
    def __init__(
        self,
        controller: ApplicationController,
        on_step_changed,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._step = WorkflowStep.STUDY1_STUDY
        self._on_step_changed = on_step_changed
        self._participant_code: str | None = None
        self._active_run = None
        self._active_live_rl = False
        self._active_remote_room = False

        root = QVBoxLayout(self)
        title = QLabel(STEP_LABELS[self._step])
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        root.addWidget(title)

        note = QLabel(
            "Study 1 investigates WHEN a human should intervene. Keyboard is the only feedback modality. "
            "Complete four conditions: Gridworld Requested, Gridworld Anytime, Continuous Requested, and Continuous Anytime."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #666; font-size: 11px;")
        root.addWidget(note)

        root.addWidget(self._build_protocol_table())
        root.addWidget(self._build_config_box())
        root.addWidget(self._build_status_box())
        root.addWidget(self._build_history_box(), 1)

        self._controller.rl_manager.trial_started.connect(self._on_trial_started)
        self._controller.rl_manager.episode_finished.connect(self._on_episode_finished)
        self._controller.rl_manager.status_changed.connect(self._on_rl_status_changed)
        self._controller.continuous_nav_client.connection_status_changed.connect(
            self._on_worker_connection_status
        )
        self._controller.continuous_nav_client.episode_ended.connect(
            self._on_remote_episode_ended
        )
        self._controller.continuous_nav_client.task_started.connect(
            self._on_remote_task_started
        )
        self._controller.continuous_nav_client.task_ended.connect(
            self._on_remote_task_ended
        )
        self._controller.continuous_nav_client.remote_error.connect(
            self._on_remote_error
        )
        self._on_task_changed()

    # ------------------------------------------------------------------
    def _build_protocol_table(self) -> QGroupBox:
        box = QGroupBox("Required Study 1 protocol sub-steps")
        layout = QVBoxLayout(box)

        top = QHBoxLayout()
        self._summary_label = QLabel("0 / 4 conditions completed")
        self._summary_label.setStyleSheet("font-weight: bold;")
        top.addWidget(self._summary_label)
        top.addStretch()
        self._mark_item_btn = QPushButton("Mark Selected Sub-step Complete…")
        self._mark_item_btn.clicked.connect(self._mark_selected_complete)
        top.addWidget(self._mark_item_btn)
        self._next_btn = QPushButton("Select Next Incomplete")
        self._next_btn.clicked.connect(self._select_next_incomplete)
        top.addWidget(self._next_btn)
        layout.addLayout(top)

        self._table = QTableWidget(len(STUDY1_STUDY_REQUIRED_CONDITIONS), 5)
        self._table.setHorizontalHeaderLabels(
            ["Protocol sub-step", "Environment", "Timing", "Last input", "Status"]
        )
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.cellClicked.connect(lambda row, _col: self._select_task_row(row))
        self._table.setMinimumHeight(180)
        layout.addWidget(self._table)

        legend = QLabel("⬜ Not started    🔶 In progress    ✅ Completed    ⚠ Needs repeat")
        legend.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(legend)
        return box

    def _build_config_box(self) -> QGroupBox:
        box = QGroupBox("Selected sub-step configuration")
        form = QFormLayout(box)

        self._task_combo = QComboBox()
        for item in STUDY1_STUDY_REQUIRED_CONDITIONS:
            self._task_combo.addItem(item.label, item.key)
        self._task_combo.currentIndexChanged.connect(self._on_task_changed)
        form.addRow("Protocol sub-step:", self._task_combo)

        self._modality_combo = QComboBox()
        for modality in EXPLICIT_STUDY1_MODALITIES:
            self._modality_combo.addItem(modality.value, modality.name)
        self._modality_combo.currentIndexChanged.connect(self._update_execution_hint)
        form.addRow("Feedback input (fixed):", self._modality_combo)


        room_cfg = self._controller.config.study_raw.get("continuous_room_navigation", {})
        worker_row = QWidget()
        worker_layout = QHBoxLayout(worker_row)
        worker_layout.setContentsMargins(0, 0, 0, 0)
        self._worker_host = QLineEdit(str(room_cfg.get("worker_host", "127.0.0.1")))
        self._worker_host.setPlaceholderText("Ubuntu IP, e.g. 192.168.1.50")
        worker_layout.addWidget(self._worker_host, 1)
        self._worker_port = QSpinBox()
        self._worker_port.setRange(1, 65535)
        self._worker_port.setValue(int(room_cfg.get("worker_port", 8875)))
        self._worker_port.setToolTip("Ubuntu HINT worker TCP/WebSocket port")
        worker_layout.addWidget(self._worker_port)
        self._worker_connect_btn = QPushButton("Connect / Test")
        self._worker_connect_btn.clicked.connect(self._connect_worker)
        worker_layout.addWidget(self._worker_connect_btn)
        form.addRow("Ubuntu worker:", worker_row)

        self._worker_status_label = QLabel("Not connected")
        self._worker_status_label.setWordWrap(True)
        self._worker_status_label.setStyleSheet("color: #666;")
        form.addRow("Worker status:", self._worker_status_label)

        self._hil_steps_spin = QSpinBox()
        self._hil_steps_spin.setRange(1, 100)
        self._hil_steps_spin.setValue(int(room_cfg.get("hil_correction_length", 10)))
        form.addRow("Study 1(b) rewind/control N:", self._hil_steps_spin)

        self._room_timeout_spin = QSpinBox()
        self._room_timeout_spin.setRange(1, 120)
        self._room_timeout_spin.setValue(int(room_cfg.get("feedback_timeout_seconds", 10)))
        self._room_timeout_spin.setSuffix(" s")
        form.addRow("Study 1(b) action timeout:", self._room_timeout_spin)

        self._seed_spin = QSpinBox()
        self._seed_spin.setRange(0, 2_147_483_647)
        self._seed_spin.setValue(int(self._controller.config.study_raw.get("random_seed", 42)))
        form.addRow("Gridworld random seed:", self._seed_spin)

        self._warm_start = QCheckBox("Use maze-informed Actor/Critic warm start")
        form.addRow("Gridworld warm start:", self._warm_start)

        self._execution_label = QLabel("")
        self._execution_label.setWordWrap(True)
        self._execution_label.setStyleSheet("color: #555;")
        form.addRow("Execution:", self._execution_label)
        return box

    def _build_status_box(self) -> QGroupBox:
        box = QGroupBox("Run")
        layout = QVBoxLayout(box)
        self._status_label = QLabel("No run in progress.")
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
        self._start_btn = QPushButton("Start Selected Sub-step")
        self._start_btn.clicked.connect(self._start_run)
        self._pause_btn = QPushButton("Pause")
        self._pause_btn.clicked.connect(self._controller.pause_active_trial)
        self._resume_btn = QPushButton("Resume")
        self._resume_btn.clicked.connect(self._controller.resume_active_trial)
        self._complete_btn = QPushButton("Stop && Mark Valid")
        self._complete_btn.clicked.connect(
            lambda: self._finish_run(CollectionRunStatus.VALID)
        )
        self._invalid_btn = QPushButton("Mark Invalid / Repeat")
        self._invalid_btn.clicked.connect(self._mark_invalid_and_repeat)
        self._abort_btn = QPushButton("Abort Run")
        self._abort_btn.clicked.connect(
            lambda: self._finish_run(CollectionRunStatus.ABORTED)
        )
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
        self._set_running_controls(False)
        return box

    def _build_history_box(self) -> QGroupBox:
        box = QGroupBox("Run History")
        layout = QVBoxLayout(box)
        self._history = QTableWidget(0, 9)
        self._history.setHorizontalHeaderLabels(
            [
                "Condition", "Attempt", "Sub-step", "Timing", "Input",
                "Result", "Started", "Ended", "Duration"
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
            self._select_next_incomplete()

    def refresh(self) -> None:
        self._refresh_protocol_table()
        self._refresh_history()
        self._update_execution_hint()

        if self._participant_code is None:
            self._status_label.setText("Select or register a participant first.")
            self._start_btn.setEnabled(False)
            self._set_running_controls(False)
            return

        summary = self._controller.workflow_manager.step_status(
            self._participant_code, self._step
        )
        self._active_run = summary.active_run
        blocking = self._controller.workflow_manager.has_active_run(self._participant_code)

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
            self._set_running_controls(True)
            return

        self._active_live_rl = False
        self._set_running_controls(False)

        if blocking is not None:
            self._status_label.setText(
                f"Another run ({blocking.run_id}) is in progress. Finish or abort it first."
            )
            self._start_btn.setEnabled(False)
        elif summary.completed_count == STUDY1_STUDY_REQUIRED_CONDITION_COUNT:
            self._status_label.setText("All required Study 1 protocol sub-steps are completed.")
            self._start_btn.setEnabled(True)  # repeats are allowed
        else:
            self._status_label.setText(
                f"Study 1 progress: {summary.completed_count}/"
                f"{STUDY1_STUDY_REQUIRED_CONDITION_COUNT} required conditions completed."
            )
            self._start_btn.setEnabled(True)

    def _refresh_protocol_table(self) -> None:
        if self._participant_code is None:
            self._summary_label.setText("Select a participant to view Study 1 status.")
            for row, req in enumerate(STUDY1_STUDY_REQUIRED_CONDITIONS):
                vals = [req.label, req.environment.value, self._timing_text(req.feedback_timing), "--", "⬜ Not Started"]
                for col, value in enumerate(vals):
                    self._table.setItem(row, col, QTableWidgetItem(value))
            self._next_btn.setEnabled(False)
            return

        statuses = self._controller.workflow_manager.study1_study_condition_statuses(
            self._participant_code
        )
        completed = 0
        for row, item in enumerate(statuses):
            if condition_status_is_complete(item.status):
                completed += 1
            values = [
                item.label,
                item.environment.value,
                self._timing_text(item.feedback_timing),
                item.last_modality.value if item.last_modality else "--",
                f"{_STATUS_SYMBOL[item.status]} {item.status}",
            ]
            for col, value in enumerate(values):
                cell = QTableWidgetItem(value)
                if col == 4:
                    cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._table.setItem(row, col, cell)

        self._summary_label.setText(
            f"{completed} / {STUDY1_STUDY_REQUIRED_CONDITION_COUNT} required conditions completed"
        )
        self._mark_item_btn.setEnabled(self._participant_code is not None and self._active_run is None)
        self._next_btn.setEnabled(self._active_run is None and completed < STUDY1_STUDY_REQUIRED_CONDITION_COUNT)
        self._highlight_selected_task()

    def _refresh_history(self) -> None:
        if self._participant_code is None:
            self._history.setRowCount(0)
            return
        trials = self._controller.trial_manager.list_trials(
            self._participant_code, study=Study.STUDY_1, practice=False
        )
        self._history.setRowCount(len(trials))
        for row, trial in enumerate(reversed(trials)):
            result = trial.collection_status.value
            if result == CollectionRunStatus.PENDING.value:
                result = trial.status.value
            vals = [
                trial.condition_code or "--",
                trial.run_code or "--",
                self._label_for_condition(trial.condition),
                trial.condition.feedback_timing.value,
                trial.condition.modality.value,
                result,
                _fmt_time(trial.started_at),
                _fmt_time(trial.ended_at),
                _fmt_duration(trial.started_at, trial.ended_at),
            ]
            for col, value in enumerate(vals):
                self._history.setItem(row, col, QTableWidgetItem(value))

    # ------------------------------------------------------------------
    def _selected_required(self):
        key = self._task_combo.currentData()
        return next(item for item in STUDY1_STUDY_REQUIRED_CONDITIONS if item.key == key)

    def _select_task_row(self, row: int) -> None:
        if 0 <= row < len(STUDY1_STUDY_REQUIRED_CONDITIONS):
            key = STUDY1_STUDY_REQUIRED_CONDITIONS[row].key
            idx = self._task_combo.findData(key)
            if idx >= 0:
                self._task_combo.setCurrentIndex(idx)

    def _highlight_selected_task(self) -> None:
        key = self._task_combo.currentData()
        for row, req in enumerate(STUDY1_STUDY_REQUIRED_CONDITIONS):
            if req.key == key:
                self._table.selectRow(row)
                return

    def _select_next_incomplete(self) -> None:
        if self._participant_code is None:
            return
        item = self._controller.workflow_manager.next_incomplete_study1_study_condition(
            self._participant_code
        )
        if item is None:
            return
        idx = self._task_combo.findData(item.key)
        if idx >= 0:
            self._task_combo.setCurrentIndex(idx)

    def _on_task_changed(self) -> None:
        req = self._selected_required()
        is_room = req.environment == Environment.CONTINUOUS_ROOM
        is_grid = req.environment == Environment.GRIDWORLD
        self._modality_combo.setEnabled(False)
        self._seed_spin.setEnabled(is_grid)
        self._warm_start.setEnabled(is_grid)
        for widget in (self._worker_host, self._worker_port, self._worker_connect_btn, self._hil_steps_spin, self._room_timeout_spin):
            widget.setEnabled(is_room and self._active_run is None)
        self._highlight_selected_task()
        self._update_execution_hint()

    def _update_execution_hint(self) -> None:
        if not hasattr(self, "_execution_label"):
            return
        req = self._selected_required()
        modality = Modality[self._modality_combo.currentData()]
        if req.environment == Environment.GRIDWORLD:
            text = "Live Actor-Critic Gridworld with Keyboard feedback."
        elif req.environment == Environment.CONTINUOUS_ROOM:
            client = self._controller.continuous_nav_client
            state = "connected" if client.connected else "not connected"
            text = (
                f"Continuous room {req.feedback_timing.value} runs on the Ubuntu HINT worker. The Console renders the live room/robot state, "
                "records Beam gaze + Shimmer, and uses Keyboard feedback. "
                f"Worker is currently {state}."
            )
        self._execution_label.setText(text)
        self._update_data_folder_preview()

    def _connect_worker(self) -> None:
        host = self._worker_host.text().strip()
        if not host:
            QMessageBox.warning(self, "Ubuntu worker", "Enter the Ubuntu PC IP/hostname first.")
            return
        self._worker_connect_btn.setEnabled(False)
        port = int(self._worker_port.value())
        self._worker_status_label.setText(f"Connecting to {host}:{port}…")
        try:
            result = self._controller.connect_continuous_nav_worker(host, port)
            status = result.get("status", {})
            clock = result.get("clock_sync", {})
            rtt_ms = float(clock.get("median_rtt_ns", 0)) / 1_000_000.0
            offset_ms = float(clock.get("median_worker_minus_console_offset_ns", 0)) / 1_000_000.0
            self._worker_status_label.setText(
                f"Connected: {status.get('hostname', host)} | running={status.get('running')} | "
                f"median RTT={rtt_ms:.2f} ms | Ubuntu−Console clock offset={offset_ms:.2f} ms"
            )
        except Exception as exc:
            self._worker_status_label.setText(f"Connection failed: {exc}")
            QMessageBox.critical(self, "Ubuntu worker connection failed", str(exc))
        finally:
            self._worker_connect_btn.setEnabled(True)
            self._update_execution_hint()

    def _on_worker_connection_status(self, status: str) -> None:
        if hasattr(self, "_worker_status_label"):
            if status.startswith("Connected"):
                clock = self._controller.continuous_nav_client.clock_sync
                if clock:
                    rtt_ms = float(clock.get("median_rtt_ns", 0)) / 1_000_000.0
                    offset_ms = float(clock.get("median_worker_minus_console_offset_ns", 0)) / 1_000_000.0
                    status = (
                        f"{status} | median RTT={rtt_ms:.2f} ms | "
                        f"Ubuntu−Console clock offset={offset_ms:.2f} ms"
                    )
            self._worker_status_label.setText(status)
            self._update_execution_hint()

    def _condition_from_selection(self) -> ExperimentCondition:
        req = self._selected_required()
        timing = req.feedback_timing
        modality = Modality.KEYBOARD
        algorithm = {
            Environment.GRIDWORLD: "actor_critic_gridworld",
            Environment.CONTINUOUS_ROOM: "ubuntu_ga3c_continuous_room",
        }[req.environment]

        return ExperimentCondition(
            study=Study.STUDY_1,
            environment=req.environment,
            feedback_timing=timing,
            modality=modality,
            rl_algorithm=algorithm,
            random_seed=(
                self._seed_spin.value()
                if req.environment == Environment.GRIDWORLD
                else None
            ),
        )

    def _update_data_folder_preview(self) -> None:
        if not hasattr(self, "_data_folder_label"):
            return
        if self._participant_code is None:
            self._data_folder_label.setText("Next data folder: --")
            return
        try:
            session_id = self._controller.session_manager.preview_session_id(
                self._participant_code
            )
            preview = self._controller.trial_manager.preview_storage(
                participant_code=self._participant_code,
                session_id=session_id,
                condition=self._condition_from_selection(),
                practice=False,
            )
            self._data_folder_label.setText(
                f"Next data folder: {preview['relative_dir']}"
            )
        except Exception:
            self._data_folder_label.setText("Next data folder: unavailable")

    # ------------------------------------------------------------------
    def _start_run(self) -> None:
        if self._participant_code is None:
            return

        req = self._selected_required()
        existing = self._controller.workflow_manager.study1_study_condition_status(
            self._participant_code, req.key
        )
        if condition_status_is_complete(existing.status):
            answer = QMessageBox.question(
                self,
                "Sub-step already completed",
                f"{req.label} is already complete (valid run or manual override). "
                "Start a repeat run anyway?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        if not self._controller.device_manager.beam_stream_healthy():
            answer = QMessageBox.question(
                self,
                "Beam is not receiving live gaze",
                "No fresh Beam eye-tracking samples are reaching HINT. If you start "
                "this Study 1 run now, no Beam gaze CSV or screen_gaze.mp4 will be recorded.\n\n"
                "Return to Devices, calibrate/connect Beam, and validate live gaze.\n\n"
                "Start without Beam gaze recording anyway?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        if not self._controller.device_manager.shimmer_stream_healthy():
            answer = QMessageBox.question(
                self,
                "Shimmer is not receiving live data",
                "No fresh Shimmer GSR/PPG samples are reaching the GUI. If you start "
                "this experimental run now, no trial-specific physiological CSV will "
                "be created.\n\nReturn to Devices, reconnect/check live data, and then start the run.\n\n"
                "Start without Shimmer physiological recording anyway?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        keyboard_ok, keyboard_message = self._controller.device_manager.check_keyboards()
        if not keyboard_ok:
            answer = QMessageBox.question(
                self,
                "Keyboard input is not verified",
                f"{keyboard_message}\n\nStart this Keyboard condition anyway?",
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

        condition = self._condition_from_selection()
        modality = condition.modality

        self._active_live_rl = req.environment == Environment.GRIDWORLD
        self._active_remote_room = req.environment == Environment.CONTINUOUS_ROOM

        try:
            if self._active_live_rl:
                trial = self._controller.start_actor_critic_trial(
                    session_id=session.session_id,
                    condition=condition,
                    practice=False,
                    use_maze_qinit=self._warm_start.isChecked(),
                )
            elif self._active_remote_room:
                if not self._controller.continuous_nav_client.connected:
                    self._controller.connect_continuous_nav_worker(
                        self._worker_host.text().strip(), self._worker_port.value()
                    )
                trial = self._controller.start_continuous_room_trial(
                    session_id=session.session_id,
                    condition=condition,
                    practice=False,
                    hil_correction_length=self._hil_steps_spin.value(),
                    feedback_timeout_seconds=self._room_timeout_spin.value(),
                )
            else:
                trial = self._controller.start_tracked_trial(
                    session_id=session.session_id,
                    condition=condition,
                    practice=False,
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

    def _mark_selected_complete(self) -> None:
        if self._participant_code is None or self._active_run is not None:
            return
        item = self._selected_required()
        status = self._controller.workflow_manager.study1_study_condition_status(
            self._participant_code, item.key
        )
        if condition_status_is_complete(status.status):
            QMessageBox.information(self, "Already complete", f"'{item.label}' is already complete.")
            return
        reason, ok = QInputDialog.getText(
            self,
            "Manual completion reason",
            f"Reason for marking '{item.label}' complete:",
        )
        if not ok:
            return
        try:
            self._controller.workflow_manager.mark_completion_override(
                self._participant_code,
                self._step,
                item_key=item.key,
                reason=reason,
            )
        except Exception as exc:
            QMessageBox.warning(self, "Could not mark sub-step complete", str(exc))
            return
        self.refresh()
        self._select_next_incomplete()
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
            self, "Mark run invalid", "Reason for repeating this condition:",
            reasons, 0, False
        )
        if not ok:
            return
        if reason == "Other":
            reason, ok = QInputDialog.getText(
                self, "Invalid run reason", "Enter reason:"
            )
            if not ok or not reason.strip():
                return
        self._finish_run(CollectionRunStatus.INVALID, reason.strip())

    def _finish_run(
        self, outcome: CollectionRunStatus, reason: str = ""
    ) -> None:
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
        self._active_remote_room = False
        self.refresh()
        if outcome == CollectionRunStatus.VALID:
            self._select_next_incomplete()
        else:
            # Keep the same condition selected; the folder preview now shows R02/R03.
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
        if hasattr(self, "_worker_host"):
            room_selected = self._selected_required().environment == Environment.CONTINUOUS_ROOM
            for widget in (self._worker_host, self._worker_port, self._worker_connect_btn, self._hil_steps_spin, self._room_timeout_spin):
                widget.setEnabled(room_selected and not running)

    # ------------------------------------------------------------------
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

    def _on_remote_task_started(self, payload: dict) -> None:
        trial = self._controller.active_trial
        if self._is_mine(trial) and trial.condition.environment == Environment.CONTINUOUS_ROOM:
            self._active_remote_room = True
            self._status_label.setText(f"In progress: {trial.readable_run_label} — Ubuntu RL running")
            self._set_running_controls(True)

    def _on_remote_task_ended(self, payload: dict) -> None:
        trial = self._controller.active_trial
        if self._is_mine(trial) and trial.condition.environment == Environment.CONTINUOUS_ROOM:
            self._status_label.setText(
                f"Ubuntu task ended ({payload.get('status') or payload.get('type')}). "
                "Mark this run Valid, Invalid/Repeat, or Abort."
            )

    def _on_remote_error(self, message: str) -> None:
        trial = self._controller.active_trial
        if trial is not None and self._is_mine(trial) and trial.condition.environment == Environment.CONTINUOUS_ROOM:
            self._status_label.setText("Ubuntu RL failure — see error dialog/log; mark the run Invalid/Repeat.")
        QMessageBox.critical(self, "Ubuntu Study 1(b) RL failure", str(message))

    def _on_remote_episode_ended(self, payload: dict) -> None:
        trial = self._controller.active_trial
        if self._is_mine(trial) and trial.condition.environment == Environment.CONTINUOUS_ROOM:
            self._episode_label.setText(f"Episode: {payload.get('episode', '--')}")
            reward = payload.get("total_reward")
            if reward is not None:
                try:
                    self._reward_label.setText(f"Last reward: {float(reward):.2f}")
                except (TypeError, ValueError):
                    self._reward_label.setText(f"Last reward: {reward}")

    @staticmethod
    def _timing_text(timing: FeedbackTiming | None) -> str:
        return "Requested or Anytime (recorded per run)" if timing is None else timing.value

    @staticmethod
    def _label_for_condition(condition: ExperimentCondition) -> str:
        if condition.environment == Environment.GRIDWORLD:
            if condition.feedback_timing == FeedbackTiming.REQUESTED:
                return "1A. Gridworld — Requested"
            if condition.feedback_timing == FeedbackTiming.ANYTIME:
                return "1B. Gridworld — Anytime"
        if condition.environment == Environment.CONTINUOUS_ROOM:
            return "1(b). Continuous action-space room navigation"
        if condition.environment == Environment.HUMAN_AGENT_BASELINE:
            return "3. Experimenter baseline"
        return condition.environment.value
