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

from PySide6.QtWidgets import (
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QStackedWidget,
    QStatusBar,
    QWidget,
)

from core.application_controller import ApplicationController
from gui.devices_page import DevicesPage
from gui.event_log_page import EventLogPage
from gui.participant_window import ParticipantWindow
from gui.workflow_page import WorkflowPage

_NAV_ITEMS = ["Devices", "Workflow", "Event Log"]

_DEVICES_INDEX = 0
_WORKFLOW_INDEX = 1
_EVENT_LOG_INDEX = 2


class MainWindow(QMainWindow):
    def __init__(self, controller: ApplicationController) -> None:
        super().__init__()
        self._controller = controller

        # The participant-facing second window (maze view + feedback
        # controls) is unchanged from V0.1 -- it reacts to rl_manager
        # signals directly and pops up whenever a Study 1 trial starts,
        # regardless of which researcher-console tab is showing.
        self._participant_window = ParticipantWindow(controller)

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
        self.setStatusBar(status_bar)

    def _build_pages(self) -> None:
        self._stack.addWidget(DevicesPage(self._controller))  # 0
        self._stack.addWidget(
            WorkflowPage(self._controller, on_manage_devices=self._go_to_devices)
        )  # 1
        self._stack.addWidget(EventLogPage(self._controller))  # 2

    def _go_to_devices(self) -> None:
        self._nav_list.setCurrentRow(_DEVICES_INDEX)

    def _on_nav_changed(self, index: int) -> None:
        if index >= 0:
            self._stack.setCurrentIndex(index)

    def closeEvent(self, event) -> None:
        self._participant_window.close()
        self._controller.shutdown()
        super().closeEvent(event)
