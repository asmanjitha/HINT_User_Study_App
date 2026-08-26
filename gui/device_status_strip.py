"""Small horizontal device-status readout, embedded at the top of the
Workflow page so device state is always visible, with a button to jump to
the full Devices page to connect/disconnect.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from core.application_controller import ApplicationController
from models.enums import DeviceStatus, DeviceType

_STATUS_COLORS = {
    DeviceStatus.DISCONNECTED: "#888888",
    DeviceStatus.CONNECTING: "#d4a017",
    DeviceStatus.CONNECTED: "#2e7d32",
    DeviceStatus.RECEIVING_DATA: "#2e7d32",
    DeviceStatus.WARNING: "#d4a017",
    DeviceStatus.ERROR: "#c0392b",
}

_SHORT_NAME = {
    DeviceType.HOLOLENS: "HoloLens",
    DeviceType.SHIMMER: "Shimmer",
    DeviceType.JOYSTICK: "Joystick",
    DeviceType.KEYBOARD: "Keyboard",
    DeviceType.MICROPHONE: "Mic",
}


class DeviceStatusStrip(QWidget):
    manage_devices_requested = Signal()

    def __init__(self, controller: ApplicationController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._controller = controller

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        layout.addWidget(QLabel("Devices:"))

        self._dots: dict[DeviceType, QLabel] = {}
        for device_type in DeviceType:
            dot = QLabel(f"\u25CF {_SHORT_NAME[device_type]}")
            self._dots[device_type] = dot
            layout.addWidget(dot)

        layout.addStretch()

        manage_btn = QPushButton("Manage Devices")
        manage_btn.clicked.connect(self.manage_devices_requested.emit)
        layout.addWidget(manage_btn)

        controller.device_manager.device_status_changed.connect(self._on_status_changed)
        self._refresh_all()

    def _refresh_all(self) -> None:
        for device_type, status in self._controller.device_manager.all_statuses().items():
            self._set_dot(device_type, status)

    def _on_status_changed(self, device_type: DeviceType, status: DeviceStatus) -> None:
        self._set_dot(device_type, status)

    def _set_dot(self, device_type: DeviceType, status: DeviceStatus) -> None:
        dot = self._dots.get(device_type)
        if dot is None:
            return
        color = _STATUS_COLORS.get(status, "#888888")
        dot.setStyleSheet(f"color: {color};")
        dot.setToolTip(f"{device_type.value}: {status.value}")
