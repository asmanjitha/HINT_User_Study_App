"""Event Log page.

A trimmed-down replacement for the old Dashboard: device status now lives
on the Devices page (and the status strip on Workflow), and current-session
context lives inside each step panel, so this page is just the live event
feed and disk usage -- useful for debugging/monitoring, out of the way
otherwise.
"""

from __future__ import annotations

import shutil

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.application_controller import ApplicationController
from models.event import StudyEvent


class EventLogPage(QWidget):
    def __init__(self, controller: ApplicationController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._controller = controller

        root = QVBoxLayout(self)

        title = QLabel("Event Log")
        title.setStyleSheet("font-size: 20px; font-weight: bold;")
        root.addWidget(title)

        info_box = QGroupBox("System")
        form = QFormLayout(info_box)
        form.addRow("Mode:", QLabel(controller.config.mode.value))
        form.addRow("Study config:", QLabel(controller.config.study_version))
        form.addRow("Session folder:", QLabel(str(controller.config.data_dir)))
        self._disk_label = QLabel("--")
        form.addRow("Free disk space:", self._disk_label)
        root.addWidget(info_box)

        event_box = QGroupBox("Live Events")
        event_layout = QVBoxLayout(event_box)
        self._event_list = QListWidget()
        event_layout.addWidget(self._event_list)
        root.addWidget(event_box, 1)

        controller.event_bus.event_published.connect(self._on_event)

        self._disk_timer = QTimer(self)
        self._disk_timer.timeout.connect(self._refresh_disk_space)
        self._disk_timer.start(5000)
        self._refresh_disk_space()

    def _on_event(self, event: StudyEvent) -> None:
        text = f"{event.timestamp:.1f}  {event.event_type.value}"
        if event.participant_id:
            text += f"  participant={event.participant_id}"
        if event.session_id:
            text += f"  session={event.session_id}"
        if event.value:
            text += f"  value={event.value}"
        self._event_list.addItem(QListWidgetItem(text))
        self._event_list.scrollToBottom()

    def _refresh_disk_space(self) -> None:
        try:
            usage = shutil.disk_usage(self._controller.config.data_dir)
            free_gb = usage.free / (1024**3)
            self._disk_label.setText(f"{free_gb:.1f} GB free")
        except OSError:
            self._disk_label.setText("unavailable")
