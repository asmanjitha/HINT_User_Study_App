"""Workflow page -- the heart of the simplified console.

Register/select a participant, then step through the fixed study sequence
(Registration -> Study 1 Training -> Study 1 Study -> Study 2 Training ->
Study 2 Study) using the left-hand menu, which shows each step's status
(Not Started / In Progress / Completed) and how many times it's been run.
Steps other than Registration can be repeated as many times as needed --
click the step again and press Start.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core.application_controller import ApplicationController
from core.workflow_manager import (
    STEP_LABELS,
    STEP_ORDER,
    STUDY1_STUDY_REQUIRED_CONDITION_COUNT,
    STUDY1_TRAINING_REQUIRED_CONDITION_COUNT,
    STUDY2_REQUIRED_CONDITION_COUNT,
)
from gui.device_status_strip import DeviceStatusStrip
from gui.participant_dialog import NewParticipantDialog
from gui.registration_panel import RegistrationPanel
from gui.study1_step_panel import Study1StepPanel
from gui.study1_study_panel import Study1StudyPanel
from gui.study2_step_panel import Study2StepPanel
from gui.study2_study_panel import Study2StudyPanel
from models.enums import EventType, StepOverallStatus, WorkflowStep
from models.event import StudyEvent

_STATUS_ICON = {
    StepOverallStatus.NOT_STARTED: "\u2b1c",  # white square
    StepOverallStatus.IN_PROGRESS: "\U0001F536",  # orange diamond
    StepOverallStatus.COMPLETED: "\u2705",  # check mark
}


class WorkflowPage(QWidget):
    def __init__(
        self,
        controller: ApplicationController,
        on_manage_devices,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._current_participant_code: str | None = None

        root = QVBoxLayout(self)

        title = QLabel("Study Workflow")
        title.setStyleSheet("font-size: 20px; font-weight: bold;")
        root.addWidget(title)

        self._device_strip = DeviceStatusStrip(controller)
        self._device_strip.manage_devices_requested.connect(on_manage_devices)
        root.addWidget(self._device_strip)

        root.addWidget(self._build_participant_bar())

        splitter = QSplitter()
        splitter.addWidget(self._build_step_menu())
        splitter.addWidget(self._build_step_panels())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, 1)

        self._refresh_participant_combo()
        self._on_participant_changed()

        controller.event_bus.event_published.connect(self._on_event)

    # -- Top bar: participant selection ---------------------------------------
    def _build_participant_bar(self) -> QWidget:
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)

        layout.addWidget(QLabel("Participant:"))

        self._participant_combo = QComboBox()
        self._participant_combo.setMinimumWidth(260)
        self._participant_combo.currentIndexChanged.connect(self._on_participant_changed)
        layout.addWidget(self._participant_combo, 1)

        new_btn = QPushButton("New Participant\u2026")
        new_btn.clicked.connect(self._on_new_participant)
        layout.addWidget(new_btn)

        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._refresh_participant_combo)
        layout.addWidget(refresh_btn)

        return bar

    # -- Left: step menu ---------------------------------------------------------
    def _build_step_menu(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)

        layout.addWidget(QLabel("<b>Steps</b>"))

        self._step_list = QListWidget()
        self._step_list.setFixedWidth(260)
        for step in STEP_ORDER:
            item = QListWidgetItem(STEP_LABELS[step])
            item.setData(Qt.ItemDataRole.UserRole, step)
            self._step_list.addItem(item)
        self._step_list.currentRowChanged.connect(self._on_step_selected)
        layout.addWidget(self._step_list, 1)

        legend = QLabel(
            f"{_STATUS_ICON[StepOverallStatus.NOT_STARTED]} Not started"
            f"&nbsp;&nbsp;{_STATUS_ICON[StepOverallStatus.IN_PROGRESS]} In progress"
            f"&nbsp;&nbsp;{_STATUS_ICON[StepOverallStatus.COMPLETED]} Completed"
        )
        legend.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(legend)

        return panel

    # -- Right: step detail panels ------------------------------------------------
    def _build_step_panels(self) -> QWidget:
        self._stack = QStackedWidget()

        self._registration_panel = RegistrationPanel(self._controller)
        self._stack.addWidget(self._registration_panel)  # index 0

        self._study1_training_panel = Study1StepPanel(
            self._controller, WorkflowStep.STUDY1_TRAINING, self._refresh_step_list
        )
        self._stack.addWidget(self._study1_training_panel)  # index 1

        self._study1_study_panel = Study1StudyPanel(
            self._controller, self._refresh_step_list
        )
        self._stack.addWidget(self._study1_study_panel)  # index 2

        self._study2_training_panel = Study2StepPanel(
            self._controller, WorkflowStep.STUDY2_TRAINING, self._refresh_step_list
        )
        self._stack.addWidget(self._study2_training_panel)  # index 3

        self._study2_study_panel = Study2StudyPanel(
            self._controller, self._refresh_step_list
        )
        self._stack.addWidget(self._study2_study_panel)  # index 4

        self._panels = [
            self._registration_panel,
            self._study1_training_panel,
            self._study1_study_panel,
            self._study2_training_panel,
            self._study2_study_panel,
        ]

        self._step_list.setCurrentRow(0)
        return self._stack

    # -- Participant selection ----------------------------------------------------
    def _refresh_participant_combo(self) -> None:
        previous = self._participant_combo.currentData()
        self._participant_combo.blockSignals(True)
        self._participant_combo.clear()
        self._participant_combo.addItem("\u2014 Select a participant \u2014", None)
        for p in self._controller.participant_manager.list_participants():
            age_part = f", age {p['age']}" if p.get("age") else ""
            label = f"{p['participant_code']} \u2014 {p['name']}{age_part}"
            self._participant_combo.addItem(label, p["participant_code"])
        self._participant_combo.blockSignals(False)

        if previous is not None:
            idx = self._participant_combo.findData(previous)
            if idx >= 0:
                self._participant_combo.setCurrentIndex(idx)
                return
        self._on_participant_changed()

    def _on_new_participant(self) -> None:
        dialog = NewParticipantDialog(self._controller, self)
        if dialog.exec() and dialog.created_participant_code:
            self._refresh_participant_combo()
            idx = self._participant_combo.findData(dialog.created_participant_code)
            if idx >= 0:
                self._participant_combo.setCurrentIndex(idx)

    def _on_participant_changed(self) -> None:
        code = self._participant_combo.currentData() if hasattr(self, "_participant_combo") else None
        self._current_participant_code = code
        for panel in getattr(self, "_panels", []):
            panel.set_participant(code)
        self._refresh_step_list()

    # -- Step menu -----------------------------------------------------------------
    def _on_step_selected(self, row: int) -> None:
        if row < 0:
            return
        self._stack.setCurrentIndex(row)
        self._panels[row].refresh()

    def _refresh_step_list(self) -> None:
        participant_exists = self._current_participant_code is not None
        if participant_exists:
            summaries = {
                s.step: s
                for s in self._controller.workflow_manager.all_step_statuses(self._current_participant_code)
            }
        else:
            summaries = {}

        for row, step in enumerate(STEP_ORDER):
            item = self._step_list.item(row)
            if not participant_exists:
                item.setText(f"{_STATUS_ICON[StepOverallStatus.NOT_STARTED]}  {STEP_LABELS[step]}")
                continue
            summary = summaries[step]
            icon = _STATUS_ICON[summary.overall_status]
            if step == WorkflowStep.STUDY1_TRAINING:
                suffix = (
                    f"  ({summary.completed_count}/{STUDY1_TRAINING_REQUIRED_CONDITION_COUNT} training conditions)"
                )
                if summary.active_run is not None:
                    suffix += "  (running)"
            elif step == WorkflowStep.STUDY1_STUDY:
                suffix = (
                    f"  ({summary.completed_count}/{STUDY1_STUDY_REQUIRED_CONDITION_COUNT} protocol conditions)"
                )
                if summary.active_run is not None:
                    suffix += "  (running)"
            elif step == WorkflowStep.STUDY2_STUDY:
                suffix = (
                    f"  ({summary.completed_count}/{STUDY2_REQUIRED_CONDITION_COUNT} modalities)"
                )
                if summary.active_run is not None:
                    suffix += "  (running)"
            else:
                suffix = (
                    f"  ({summary.completed_count} completed)"
                    if summary.completed_count
                    else ""
                )
                if summary.overall_status == StepOverallStatus.IN_PROGRESS:
                    suffix = "  (running)"
            item.setText(f"{icon}  {STEP_LABELS[step]}{suffix}")

        # Also refresh whichever panel is currently visible, in case its
        # status changed as a side effect of an action elsewhere.
        current_row = self._step_list.currentRow()
        if current_row >= 0:
            self._panels[current_row].refresh()

    def _on_event(self, event: StudyEvent) -> None:
        if event.event_type in (EventType.SESSION_CREATED, EventType.SESSION_STARTED, EventType.SESSION_ENDED):
            self._refresh_step_list()
