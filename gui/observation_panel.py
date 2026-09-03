"""Researcher controls for Study 3 prerecorded agent-observation videos."""

from __future__ import annotations

import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.application_controller import ApplicationController
from core.config_loader import PROJECT_ROOT
from core.observation_video_settings import (
    load_observation_video_paths,
    resolve_observation_video_path,
    save_observation_video_paths,
)
from core.workflow_manager import (
    OBSERVATION_REQUIRED_CONDITIONS,
    OBSERVATION_REQUIRED_CONDITION_COUNT,
    STEP_LABELS,
    condition_status_is_complete,
)
from models.enums import (
    CollectionRunStatus,
    Environment,
    EventType,
    FeedbackTiming,
    Modality,
    Study,
    WorkflowStep,
)
from models.event import StudyEvent
from models.trial import ExperimentCondition


_STATUS_SYMBOL = {
    "Not Started": "⬜",
    "In Progress": "🔶",
    "Completed": "✅",
    "Manually Completed": "✅",
    "Needs Repeat": "⚠",
}

_VIDEO_KEY_BY_ENVIRONMENT = {
    Environment.GRIDWORLD: "gridworld",
    Environment.CONTINUOUS_ROOM: "continuous_room",
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
        self._video_paths = load_observation_video_paths(controller.config.config_dir)

        root = QVBoxLayout(self)
        title = QLabel(STEP_LABELS[self._step])
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        root.addWidget(title)

        note = QLabel(
            "Study 3 uses two prerecorded agent-training videos. The participant "
            "starts each activity from the participant display and watches it in "
            "fullscreen without giving feedback. HoloLens PV/EET and Shimmer GSR/PPG "
            "start with the video and stop when it finishes."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #666; font-size: 11px;")
        root.addWidget(note)

        root.addWidget(self._build_conditions())
        root.addWidget(self._build_config())
        root.addWidget(self._build_status())
        root.addWidget(self._build_history(), 1)

        controller.event_bus.event_published.connect(self._on_lifecycle_event)

    def _build_conditions(self) -> QGroupBox:
        box = QGroupBox("Required Study 3 video observations")
        layout = QVBoxLayout(box)
        top = QHBoxLayout()
        self._summary_label = QLabel(f"0 / {OBSERVATION_REQUIRED_CONDITION_COUNT} completed")
        self._summary_label.setStyleSheet("font-weight: bold;")
        top.addWidget(self._summary_label)
        top.addStretch()
        self._mark_item_btn = QPushButton("Mark Selected Observation Complete…")
        self._mark_item_btn.clicked.connect(self._mark_selected_complete)
        top.addWidget(self._mark_item_btn)
        self._next_btn = QPushButton("Select Next Incomplete")
        self._next_btn.clicked.connect(self._select_next)
        top.addWidget(self._next_btn)
        layout.addLayout(top)

        self._table = QTableWidget(len(OBSERVATION_REQUIRED_CONDITIONS), 3)
        self._table.setHorizontalHeaderLabels(["Video", "Participant task", "Status"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.cellClicked.connect(self._condition_clicked)
        for row, req in enumerate(OBSERVATION_REQUIRED_CONDITIONS):
            self._table.setItem(row, 0, QTableWidgetItem(req.label))
            task = QTableWidgetItem("Watch only — no feedback")
            task.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 1, task)
            self._table.setItem(row, 2, QTableWidgetItem("⬜ Not Started"))
        self._table.setMinimumHeight(125)
        layout.addWidget(self._table)
        return box

    def _build_config(self) -> QGroupBox:
        box = QGroupBox("Observation video setup")
        form = QFormLayout(box)

        self._condition_combo = QComboBox()
        for req in OBSERVATION_REQUIRED_CONDITIONS:
            self._condition_combo.addItem(req.label, req.key)
        self._condition_combo.currentIndexChanged.connect(self._selection_changed)
        form.addRow("Selected video:", self._condition_combo)

        self._video_edits: dict[str, QLineEdit] = {}
        labels = {
            "gridworld": "Gridworld video:",
            "continuous_room": "Room-environment video:",
        }
        for key in ("gridworld", "continuous_room"):
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            edit = QLineEdit(self._video_paths.get(key, ""))
            edit.setPlaceholderText("Choose an MP4 file on this PC")
            edit.editingFinished.connect(self._save_video_paths_from_fields)
            self._video_edits[key] = edit
            row_layout.addWidget(edit, 1)
            browse = QPushButton("Browse…")
            browse.clicked.connect(lambda _checked=False, k=key: self._browse_video(k))
            row_layout.addWidget(browse)
            form.addRow(labels[key], row)

        self._sensor_label = QLabel("")
        self._sensor_label.setWordWrap(True)
        form.addRow("Required sensors:", self._sensor_label)

        self._execution_label = QLabel(
            "Researcher prepares the run → participant presses Start Activity → "
            "video and sensors begin → natural video end is marked Valid automatically."
        )
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

        buttons = QHBoxLayout()
        self._start_btn = QPushButton("Prepare Selected Video")
        self._start_btn.clicked.connect(self._start_run)
        self._complete_btn = QPushButton("Stop && Mark Valid")
        self._complete_btn.clicked.connect(lambda: self._finish_run(CollectionRunStatus.VALID))
        self._invalid_btn = QPushButton("Mark Invalid / Repeat")
        self._invalid_btn.clicked.connect(self._mark_invalid_and_repeat)
        self._abort_btn = QPushButton("Abort Run")
        self._abort_btn.clicked.connect(lambda: self._finish_run(CollectionRunStatus.ABORTED))
        for button in (self._start_btn, self._complete_btn, self._invalid_btn, self._abort_btn):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self._set_running_controls(False)
        return box

    def _build_history(self) -> QGroupBox:
        box = QGroupBox("Observation Run History")
        layout = QVBoxLayout(box)
        self._history = QTableWidget(0, 8)
        self._history.setHorizontalHeaderLabels(
            ["Condition", "Attempt", "Video", "Task", "Result", "Started", "Ended", "Duration"]
        )
        self._history.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._history.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self._history)
        return box

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
            state = "playing" if self._controller.activity_started else "waiting for participant Start"
            self._status_label.setText(f"In progress: {label} — {state}")
            if trial is not None and trial.trial_path is not None:
                try:
                    rel = trial.trial_path.relative_to(self._controller.config.data_dir)
                except ValueError:
                    rel = trial.trial_path
                self._data_folder_label.setText(f"Current data folder: {rel}")
            self._start_btn.setEnabled(False)
            self._set_running_controls(True)
            return

        self._set_running_controls(False)
        if blocking is not None:
            self._status_label.setText(
                f"Another run ({blocking.run_id}) is in progress. Finish or abort it first."
            )
            self._start_btn.setEnabled(False)
        elif summary.completed_count == OBSERVATION_REQUIRED_CONDITION_COUNT:
            self._status_label.setText("Study 3 is complete: both videos have valid recordings.")
            self._start_btn.setEnabled(True)
        else:
            self._status_label.setText(
                f"Study 3 progress: {summary.completed_count}/"
                f"{OBSERVATION_REQUIRED_CONDITION_COUNT} videos completed."
            )
            self._start_btn.setEnabled(True)

    def _refresh_table(self) -> None:
        if self._participant_code is None:
            self._summary_label.setText("Select a participant to view Study 3 status.")
            self._next_btn.setEnabled(False)
            self._mark_item_btn.setEnabled(False)
            return
        statuses = self._controller.workflow_manager.observation_condition_statuses(self._participant_code)
        completed = 0
        for row, item in enumerate(statuses):
            if condition_status_is_complete(item.status):
                completed += 1
            self._table.setItem(row, 0, QTableWidgetItem(item.label))
            task = QTableWidgetItem("Watch only — no feedback")
            task.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 1, task)
            status = QTableWidgetItem(f"{_STATUS_SYMBOL[item.status]} {item.status}")
            status.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 2, status)
        self._summary_label.setText(f"{completed} / {OBSERVATION_REQUIRED_CONDITION_COUNT} videos completed")
        self._mark_item_btn.setEnabled(self._active_run is None)
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
            values = [
                trial.condition_code or "--",
                trial.run_code or "--",
                trial.condition.environment.value,
                "Watch video",
                result,
                _fmt_time(trial.started_at),
                _fmt_time(trial.ended_at),
                _fmt_duration(trial.started_at, trial.ended_at),
            ]
            for col, value in enumerate(values):
                self._history.setItem(row, col, QTableWidgetItem(value))

    def _refresh_sensor_status(self) -> None:
        shimmer = self._controller.device_manager.shimmer_stream_healthy()
        hololens = self._controller.device_manager.hololens_stream_healthy()
        self._sensor_label.setText(
            f"Shimmer GSR/PPG: {'READY' if shimmer else 'NOT READY'}   |   "
            f"HoloLens PV/EET: {'READY' if hololens else 'NOT READY'}"
        )

    def _selected_required(self):
        key = self._condition_combo.currentData()
        for req in OBSERVATION_REQUIRED_CONDITIONS:
            if req.key == key:
                return req
        return OBSERVATION_REQUIRED_CONDITIONS[0]

    def _selected_video_key(self) -> str:
        return _VIDEO_KEY_BY_ENVIRONMENT[self._selected_required().environment]

    def _selected_video_path(self) -> Path:
        self._save_video_paths_from_fields()
        raw = self._video_paths.get(self._selected_video_key(), "")
        if not raw:
            raise ValueError("Select the video file for this Study 3 activity first.")
        path = resolve_observation_video_path(raw, PROJECT_ROOT)
        if not path.is_file():
            raise FileNotFoundError(f"The selected observation video does not exist:\n{path}")
        return path

    def _browse_video(self, key: str) -> None:
        current = self._video_edits[key].text().strip()
        start = str(Path(current).parent) if current else str(PROJECT_ROOT)
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "Select Study 3 observation video",
            start,
            "Video files (*.mp4 *.mov *.m4v *.avi *.mkv);;All files (*)",
        )
        if not selected:
            return
        self._video_edits[key].setText(selected)
        self._save_video_paths_from_fields()
        self._selection_changed()

    def _save_video_paths_from_fields(self) -> None:
        if not hasattr(self, "_video_edits"):
            return
        self._video_paths = {key: edit.text().strip() for key, edit in self._video_edits.items()}
        try:
            save_observation_video_paths(self._controller.config.config_dir, self._video_paths)
        except OSError as exc:
            QMessageBox.warning(self, "Could not save video paths", str(exc))

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
        item = self._controller.workflow_manager.next_incomplete_observation_condition(self._participant_code)
        if item is not None:
            idx = self._condition_combo.findData(item.key)
            if idx >= 0:
                self._condition_combo.setCurrentIndex(idx)

    def _selection_changed(self) -> None:
        if not hasattr(self, "_condition_combo"):
            return
        self._highlight_selected()
        self._update_data_folder_preview()

    def _condition_from_selection(self) -> ExperimentCondition:
        req = self._selected_required()
        algorithm = (
            "prerecorded_gridworld_training_video"
            if req.environment == Environment.GRIDWORLD
            else "prerecorded_continuous_room_training_video"
        )
        return ExperimentCondition(
            study=Study.OBSERVATION,
            environment=req.environment,
            feedback_timing=FeedbackTiming.NOT_APPLICABLE,
            modality=Modality.NONE,
            rl_algorithm=algorithm,
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
            _ok, message = self._controller.device_manager.check_hololens()
            details.append(f"• HoloLens stream is not ready: {message}")
        QMessageBox.warning(
            self,
            "Required Study 3 sensors are not ready",
            "Both Shimmer GSR/PPG and HoloLens PV/EET are required.\n\n"
            + "\n".join(details)
            + "\n\nReconnect/check the devices before preparing the video.",
        )
        return False

    def _start_run(self) -> None:
        if self._participant_code is None:
            return
        try:
            video_path = self._selected_video_path()
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "Observation video unavailable", str(exc))
            return
        if not self._check_required_sensors():
            return

        req = self._selected_required()
        existing = self._controller.workflow_manager.observation_condition_status(
            self._participant_code, req.key
        )
        if condition_status_is_complete(existing.status):
            answer = QMessageBox.question(
                self,
                "Observation already completed",
                f"{req.label} is already complete. Start a repeat anyway?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        run = None
        try:
            run, session = self._controller.workflow_manager.start_run(self._participant_code, self._step)
            trial = self._controller.prepare_observation_video_trial(
                session.session_id,
                self._condition_from_selection(),
                video_path,
                practice=False,
            )
            self._controller.workflow_manager.attach_trial(run.run_id, trial.trial_id)
            self._active_run = self._controller.workflow_manager.get_run(run.run_id)
        except Exception as exc:
            if run is not None:
                self._controller.workflow_manager.end_run(
                    run.run_id, completed=False, notes=f"Failed to prepare video: {exc}"
                )
            QMessageBox.critical(self, "Could not prepare Study 3 video", str(exc))
            self.refresh()
            return
        self.refresh()

    def _mark_selected_complete(self) -> None:
        if self._participant_code is None or self._active_run is not None:
            return
        item = self._selected_required()
        status = self._controller.workflow_manager.observation_condition_status(
            self._participant_code, item.key
        )
        if condition_status_is_complete(status.status):
            QMessageBox.information(self, "Already complete", f"'{item.label}' is already complete.")
            return
        reason, ok = QInputDialog.getText(
            self, "Manual completion reason", f"Reason for marking '{item.label}' complete:"
        )
        if not ok:
            return
        try:
            self._controller.workflow_manager.mark_completion_override(
                self._participant_code, self._step, item_key=item.key, reason=reason
            )
        except Exception as exc:
            QMessageBox.warning(self, "Could not mark observation complete", str(exc))
            return
        self.refresh()
        self._select_next()
        if self._on_step_changed:
            self._on_step_changed()

    def _mark_invalid_and_repeat(self) -> None:
        reasons = [
            "Sensor recording problem",
            "Video playback problem",
            "Equipment failure",
            "Shimmer disconnected",
            "HoloLens disconnected",
            "Experimenter error",
            "Software error",
            "Other",
        ]
        reason, ok = QInputDialog.getItem(
            self, "Mark observation invalid", "Reason for repeating this video:", reasons, 0, False
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
        self.refresh()
        if outcome == CollectionRunStatus.VALID:
            self._select_next()
        if self._on_step_changed:
            self._on_step_changed()

    def _set_running_controls(self, running: bool) -> None:
        self._complete_btn.setEnabled(running)
        self._invalid_btn.setEnabled(running)
        self._abort_btn.setEnabled(running)
        self._next_btn.setEnabled(not running and self._participant_code is not None)

    def _on_lifecycle_event(self, event: StudyEvent) -> None:
        trial = self._controller.active_trial
        belongs = bool(
            trial is not None
            and trial.condition.study == Study.OBSERVATION
            and trial.trial_id == event.trial_id
        )
        if belongs and event.event_type == EventType.PARTICIPANT_ACTIVITY_STARTED:
            self._status_label.setText("Video playing — HoloLens and Shimmer are recording.")
        elif belongs and event.event_type == EventType.OBSERVATION_VIDEO_ERROR:
            self._status_label.setText(f"Video playback error: {event.value}")
        elif event.event_type in (EventType.OBSERVATION_VIDEO_ENDED, EventType.TRIAL_ENDED):
            QTimer.singleShot(0, self._refresh_after_lifecycle_end)

    def _refresh_after_lifecycle_end(self) -> None:
        self.refresh()
        if self._on_step_changed:
            self._on_step_changed()
