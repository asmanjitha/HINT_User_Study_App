"""Final no-feedback Agent Observation phase.

Participants only observe the RL agent learning.  Both Gridworld and Continuous
Room Navigation are required.  Unlike the earlier experimental phases, this
panel enforces fresh Shimmer GSR/PPG and HoloLens streams before either run can
start because those recordings are part of the requested observation dataset.
"""

from __future__ import annotations

import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
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
    OBSERVATION_REQUIRED_CONDITIONS,
    OBSERVATION_REQUIRED_CONDITION_COUNT,
    STEP_LABELS,
)
from models.enums import (
    CollectionRunStatus,
    Environment,
    FeedbackTiming,
    Modality,
    StepOverallStatus,
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


class ObservationPanel(QWidget):
    def __init__(
        self,
        controller: ApplicationController,
        on_step_changed,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._step = WorkflowStep.AGENT_OBSERVATION
        self._on_step_changed = on_step_changed
        self._participant_code: str | None = None
        self._active_run = None
        self._active_grid = False
        self._active_room = False

        root = QVBoxLayout(self)
        title = QLabel(STEP_LABELS[self._step])
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        root.addWidget(title)

        note = QLabel(
            "Final observation phase: the participant only watches an RL agent learn. "
            "No human feedback is requested or accepted. Complete both Gridworld and "
            "Continuous Action Space runs. Fresh Shimmer GSR/PPG and HoloLens streams "
            "are required before each run so both physiological and HoloLens data are recorded."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #666; font-size: 11px;")
        root.addWidget(note)

        root.addWidget(self._build_conditions())
        root.addWidget(self._build_config())
        root.addWidget(self._build_status())
        root.addWidget(self._build_history(), 1)

        self._controller.rl_manager.trial_started.connect(self._on_trial_started)
        self._controller.rl_manager.episode_finished.connect(self._on_episode_finished)
        self._controller.rl_manager.status_changed.connect(self._on_rl_status_changed)
        self._controller.continuous_nav_client.connection_status_changed.connect(
            self._on_worker_connection_status
        )

    def _build_conditions(self) -> QGroupBox:
        box = QGroupBox("Required no-feedback observation runs")
        layout = QVBoxLayout(box)
        top = QHBoxLayout()
        self._summary_label = QLabel(f"0 / {OBSERVATION_REQUIRED_CONDITION_COUNT} completed")
        self._summary_label.setStyleSheet("font-weight: bold;")
        top.addWidget(self._summary_label)
        top.addStretch()
        self._next_btn = QPushButton("Select Next Incomplete")
        self._next_btn.clicked.connect(self._select_next)
        top.addWidget(self._next_btn)
        layout.addLayout(top)

        self._table = QTableWidget(len(OBSERVATION_REQUIRED_CONDITIONS), 3)
        self._table.setHorizontalHeaderLabels(["Environment", "Human feedback", "Status"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.cellClicked.connect(self._condition_clicked)
        for row, req in enumerate(OBSERVATION_REQUIRED_CONDITIONS):
            self._table.setItem(row, 0, QTableWidgetItem(req.label))
            no_feedback = QTableWidgetItem("None — observe only")
            no_feedback.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 1, no_feedback)
            self._table.setItem(row, 2, QTableWidgetItem("⬜ Not Started"))
        self._table.setMinimumHeight(125)
        layout.addWidget(self._table)
        return box

    def _build_config(self) -> QGroupBox:
        box = QGroupBox("Selected observation run")
        form = QFormLayout(box)

        self._condition_combo = QComboBox()
        for req in OBSERVATION_REQUIRED_CONDITIONS:
            self._condition_combo.addItem(req.label, req.key)
        self._condition_combo.currentIndexChanged.connect(self._selection_changed)
        form.addRow("Environment:", self._condition_combo)

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
        worker_layout.addWidget(self._worker_port)
        self._worker_connect_btn = QPushButton("Connect / Test")
        self._worker_connect_btn.clicked.connect(self._connect_worker)
        worker_layout.addWidget(self._worker_connect_btn)
        form.addRow("Ubuntu worker:", worker_row)

        self._worker_status_label = QLabel("Not connected")
        self._worker_status_label.setWordWrap(True)
        self._worker_status_label.setStyleSheet("color: #666;")
        form.addRow("Worker status:", self._worker_status_label)

        self._seed_spin = QSpinBox()
        self._seed_spin.setRange(0, 2_147_483_647)
        self._seed_spin.setValue(int(self._controller.config.study_raw.get("random_seed", 42)))
        form.addRow("Gridworld random seed:", self._seed_spin)

        self._warm_start = QCheckBox("Use maze-informed Actor/Critic warm start")
        form.addRow("Gridworld warm start:", self._warm_start)

        self._sensor_label = QLabel("")
        self._sensor_label.setWordWrap(True)
        form.addRow("Required sensors:", self._sensor_label)

        self._execution_label = QLabel("")
        self._execution_label.setWordWrap(True)
        self._execution_label.setStyleSheet("color: #555;")
        form.addRow("Execution:", self._execution_label)
        return box

    def _build_status(self) -> QGroupBox:
        box = QGroupBox("Observation run")
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
        self._start_btn = QPushButton("Start Selected Observation")
        self._start_btn.clicked.connect(self._start_run)
        self._pause_btn = QPushButton("Pause Gridworld")
        self._pause_btn.clicked.connect(self._controller.pause_active_trial)
        self._resume_btn = QPushButton("Resume Gridworld")
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
        self._set_running_controls(False)
        return box

    def _build_history(self) -> QGroupBox:
        box = QGroupBox("Observation Run History")
        layout = QVBoxLayout(box)
        self._history = QTableWidget(0, 8)
        self._history.setHorizontalHeaderLabels(
            ["Condition", "Attempt", "Environment", "Feedback", "Result", "Started", "Ended", "Duration"]
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
        self._refresh_table()
        self._refresh_history()
        self._refresh_sensor_status()
        self._selection_changed()

        if self._participant_code is None:
            self._status_label.setText("Select or register a participant first.")
            self._start_btn.setEnabled(False)
            self._set_running_controls(False)
            return

        summary = self._controller.workflow_manager.step_status(self._participant_code, self._step)
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

        self._active_grid = False
        self._active_room = False
        self._set_running_controls(False)

        study2 = self._controller.workflow_manager.step_status(
            self._participant_code, WorkflowStep.STUDY2_STUDY
        )
        if blocking is not None:
            self._status_label.setText(
                f"Another run ({blocking.run_id}) is in progress. Finish or abort it first."
            )
            self._start_btn.setEnabled(False)
        elif study2.overall_status != StepOverallStatus.COMPLETED:
            self._status_label.setText(
                "Finish Study 2 before starting the final Agent Observation phase."
            )
            self._start_btn.setEnabled(False)
        elif summary.completed_count == OBSERVATION_REQUIRED_CONDITION_COUNT:
            self._status_label.setText(
                "Agent Observation is complete: both no-feedback environments have valid recordings."
            )
            self._start_btn.setEnabled(True)  # allow deliberate repeats
        else:
            self._status_label.setText(
                f"Observation progress: {summary.completed_count}/"
                f"{OBSERVATION_REQUIRED_CONDITION_COUNT} environments completed."
            )
            self._start_btn.setEnabled(True)

    def _refresh_table(self) -> None:
        if self._participant_code is None:
            self._summary_label.setText("Select a participant to view observation status.")
            self._next_btn.setEnabled(False)
            return
        statuses = self._controller.workflow_manager.observation_condition_statuses(
            self._participant_code
        )
        completed = 0
        for row, item in enumerate(statuses):
            if item.status == "Completed":
                completed += 1
            self._table.setItem(row, 0, QTableWidgetItem(item.label))
            no_feedback = QTableWidgetItem("None — observe only")
            no_feedback.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 1, no_feedback)
            status = QTableWidgetItem(f"{_STATUS_SYMBOL[item.status]} {item.status}")
            status.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 2, status)
        self._summary_label.setText(
            f"{completed} / {OBSERVATION_REQUIRED_CONDITION_COUNT} environments completed"
        )
        self._next_btn.setEnabled(self._active_run is None and completed < OBSERVATION_REQUIRED_CONDITION_COUNT)
        self._highlight_selected()

    def _refresh_history(self) -> None:
        if self._participant_code is None:
            self._history.setRowCount(0)
            return
        trials = self._controller.trial_manager.list_trials(
            self._participant_code, study=Study.OBSERVATION, practice=False
        )
        self._history.setRowCount(len(trials))
        for row, trial in enumerate(reversed(trials)):
            result = trial.collection_status.value
            if result == CollectionRunStatus.PENDING.value:
                result = trial.status.value
            vals = [
                trial.condition_code or "--",
                trial.run_code or "--",
                trial.condition.environment.value,
                "No human feedback",
                result,
                _fmt_time(trial.started_at),
                _fmt_time(trial.ended_at),
                _fmt_duration(trial.started_at, trial.ended_at),
            ]
            for col, value in enumerate(vals):
                self._history.setItem(row, col, QTableWidgetItem(value))

    def _refresh_sensor_status(self) -> None:
        shimmer = self._controller.device_manager.shimmer_stream_healthy()
        hololens = self._controller.device_manager.hololens_stream_healthy()
        self._sensor_label.setText(
            f"Shimmer GSR/PPG: {'READY' if shimmer else 'NOT READY'}   |   "
            f"HoloLens: {'READY' if hololens else 'NOT READY'}"
        )

    def _selected_required(self):
        key = self._condition_combo.currentData()
        for req in OBSERVATION_REQUIRED_CONDITIONS:
            if req.key == key:
                return req
        return OBSERVATION_REQUIRED_CONDITIONS[0]

    def _condition_clicked(self, row: int, _col: int) -> None:
        if 0 <= row < len(OBSERVATION_REQUIRED_CONDITIONS):
            idx = self._condition_combo.findData(OBSERVATION_REQUIRED_CONDITIONS[row].key)
            if idx >= 0:
                self._condition_combo.setCurrentIndex(idx)

    def _highlight_selected(self) -> None:
        key = self._condition_combo.currentData()
        for row, req in enumerate(OBSERVATION_REQUIRED_CONDITIONS):
            if req.key == key:
                self._table.setCurrentCell(row, 0)
                break

    def _select_next(self) -> None:
        if self._participant_code is None:
            return
        item = self._controller.workflow_manager.next_incomplete_observation_condition(
            self._participant_code
        )
        if item is None:
            return
        idx = self._condition_combo.findData(item.key)
        if idx >= 0:
            self._condition_combo.setCurrentIndex(idx)

    def _selection_changed(self) -> None:
        if not hasattr(self, "_execution_label"):
            return
        req = self._selected_required()
        room = req.environment == Environment.CONTINUOUS_ROOM
        for widget in (self._worker_host, self._worker_port, self._worker_connect_btn):
            widget.setEnabled(room)
        self._execution_label.setText(
            "Local Actor-Critic Gridworld runs autonomously with no feedback requests."
            if not room
            else "Ubuntu Continuous Room worker runs in no-feedback observation mode; "
                 "the Console will not solicit participant actions."
        )
        self._highlight_selected()
        self._update_data_folder_preview()

    def _condition_from_selection(self) -> ExperimentCondition:
        req = self._selected_required()
        algorithm = (
            "actor_critic_gridworld_no_feedback"
            if req.environment == Environment.GRIDWORLD
            else "ubuntu_ga3c_continuous_room_no_feedback"
        )
        return ExperimentCondition(
            study=Study.OBSERVATION,
            environment=req.environment,
            feedback_timing=FeedbackTiming.NOT_APPLICABLE,
            modality=Modality.NONE,
            rl_algorithm=algorithm,
            random_seed=self._seed_spin.value() if req.environment == Environment.GRIDWORLD else None,
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

    def _connect_worker(self) -> None:
        host = self._worker_host.text().strip()
        if not host:
            QMessageBox.warning(self, "Worker host missing", "Enter the Ubuntu worker IP/hostname.")
            return
        self._worker_connect_btn.setEnabled(False)
        self._worker_status_label.setText(f"Connecting to {host}:{self._worker_port.value()}…")
        try:
            result = self._controller.connect_continuous_nav_worker(host, self._worker_port.value())
            status = result.get("status", {})
            clock = result.get("clock_sync", {})
            rtt_ms = float(clock.get("median_rtt_ns", 0)) / 1_000_000.0
            offset_ms = float(clock.get("median_worker_minus_console_offset_ns", 0)) / 1_000_000.0
            self._worker_status_label.setText(
                f"Connected: {status.get('hostname', host)} | median RTT={rtt_ms:.2f} ms | "
                f"Ubuntu−Console clock offset={offset_ms:.2f} ms"
            )
        except Exception as exc:
            self._worker_status_label.setText(f"Connection failed: {exc}")
            QMessageBox.critical(self, "Ubuntu worker connection failed", str(exc))
        finally:
            self._worker_connect_btn.setEnabled(True)

    def _on_worker_connection_status(self, status: str) -> None:
        if hasattr(self, "_worker_status_label"):
            self._worker_status_label.setText(status)

    def _check_required_sensors(self) -> bool:
        shimmer_ok = self._controller.device_manager.shimmer_stream_healthy()
        hololens_ok = self._controller.device_manager.hololens_stream_healthy()
        self._refresh_sensor_status()
        if shimmer_ok and hololens_ok:
            return True

        details: list[str] = []
        if not shimmer_ok:
            details.append("• Shimmer is not delivering fresh GSR/PPG samples.")
        if not hololens_ok:
            ok, message = self._controller.device_manager.check_hololens()
            details.append(f"• HoloLens stream is not ready: {message}")
        QMessageBox.warning(
            self,
            "Required observation sensors are not ready",
            "This phase requires both Shimmer GSR/PPG and HoloLens recording.\n\n"
            + "\n".join(details)
            + "\n\nReconnect/check the devices before starting. The run has not been started.",
        )
        return False

    def _start_run(self) -> None:
        if self._participant_code is None:
            return

        study2 = self._controller.workflow_manager.step_status(
            self._participant_code, WorkflowStep.STUDY2_STUDY
        )
        if study2.overall_status != StepOverallStatus.COMPLETED:
            QMessageBox.warning(self, "Study 2 incomplete", "Finish Study 2 before Agent Observation.")
            return
        if not self._check_required_sensors():
            return

        req = self._selected_required()
        existing = self._controller.workflow_manager.observation_condition_status(
            self._participant_code, req.key
        )
        if existing.status == "Completed":
            answer = QMessageBox.question(
                self,
                "Observation already completed",
                f"{req.label} already has a valid run. Start a repeat anyway?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        try:
            run, session = self._controller.workflow_manager.start_run(
                self._participant_code, self._step
            )
        except Exception as exc:
            QMessageBox.critical(self, "Could not start observation run", str(exc))
            return

        condition = self._condition_from_selection()
        self._active_grid = req.environment == Environment.GRIDWORLD
        self._active_room = req.environment == Environment.CONTINUOUS_ROOM
        room_cfg = self._controller.config.study_raw.get("continuous_room_navigation", {})
        try:
            if self._active_grid:
                trial = self._controller.start_actor_critic_trial(
                    session_id=session.session_id,
                    condition=condition,
                    practice=False,
                    use_maze_qinit=self._warm_start.isChecked(),
                )
            else:
                if not self._controller.continuous_nav_client.connected:
                    self._controller.connect_continuous_nav_worker(
                        self._worker_host.text().strip(), self._worker_port.value()
                    )
                trial = self._controller.start_continuous_room_trial(
                    session_id=session.session_id,
                    condition=condition,
                    practice=False,
                    hil_correction_length=int(room_cfg.get("hil_correction_length", 10)),
                    feedback_timeout_seconds=float(room_cfg.get("feedback_timeout_seconds", 10)),
                )
        except Exception as exc:
            self._controller.workflow_manager.end_run(
                run.run_id, completed=False, notes=f"Failed to start: {exc}"
            )
            QMessageBox.critical(self, "Could not start observation trial", str(exc))
            self.refresh()
            return

        self._controller.workflow_manager.attach_trial(run.run_id, trial.trial_id)
        self._active_run = self._controller.workflow_manager.get_run(run.run_id)
        self.refresh()

    def _mark_invalid_and_repeat(self) -> None:
        reasons = [
            "Sensor recording problem",
            "Equipment failure",
            "Shimmer disconnected",
            "HoloLens disconnected",
            "Experimenter error",
            "Software error",
            "Other",
        ]
        reason, ok = QInputDialog.getItem(
            self, "Mark observation invalid", "Reason for repeating this run:", reasons, 0, False
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
        self._active_grid = False
        self._active_room = False
        self.refresh()
        if outcome == CollectionRunStatus.VALID:
            self._select_next()
        if self._on_step_changed:
            self._on_step_changed()

    def _set_running_controls(self, running: bool) -> None:
        self._pause_btn.setEnabled(running and self._active_grid)
        self._resume_btn.setEnabled(running and self._active_grid)
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
