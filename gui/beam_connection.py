"""Guided Beam Eye Tracker connection and live validation panel."""

from __future__ import annotations

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.application_controller import ApplicationController
from models.enums import DeviceStatus, DeviceType

_COLORS = {
    DeviceStatus.DISCONNECTED: "#888888",
    DeviceStatus.CONNECTING: "#d4a017",
    DeviceStatus.CONNECTED: "#1976d2",
    DeviceStatus.RECEIVING_DATA: "#2e7d32",
    DeviceStatus.WARNING: "#d4a017",
    DeviceStatus.ERROR: "#c0392b",
}


class BeamConnectionPanel(QGroupBox):
    """Select the participant display and verify live Beam gaze output."""

    def __init__(
        self, controller: ApplicationController, parent: QWidget | None = None
    ) -> None:
        super().__init__("Beam Webcam Eye Tracker — Training, Study 1, and Study 2", parent)
        self._controller = controller
        root = QVBoxLayout(self)
        root.setSpacing(9)

        intro = QLabel(
            "Beam uses the study webcam and sends gaze results to HINT through the "
            "Beam Python SDK. HINT saves synchronized gaze coordinates and head pose "
            "plus an MP4 of the participant's activity display with a gaze-pointer overlay "
            "under <b>sensors/beam/</b>. HINT does <b>not</b> open or record the webcam. "
            "HoloLens recording is reserved for Agent Observation."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        steps = QLabel(
            "<b>Before connecting</b><br>"
            "1. Install and open Beam Eye Tracker 2.6.3 or newer.<br>"
            "2. In Beam, select the webcam, set its monitor position, and complete calibration.<br>"
            "3. Keep <b>Automatic — follow participant activity window</b> selected (recommended). "
            "At participant Start, HINT detects the monitor containing the user window and records "
            "that physical display. Or choose a specific display to lock recording manually.<br>"
            "4. Ask the participant to look around that display and click Validate Live Gaze."
        )
        steps.setWordWrap(True)
        root.addWidget(steps)

        display_row = QHBoxLayout()
        display_row.addWidget(QLabel("Screen recording target:"))
        self._display = QComboBox()
        display_row.addWidget(self._display, 1)
        self._refresh_displays = QPushButton("Refresh Displays")
        self._refresh_displays.clicked.connect(self._load_displays)
        display_row.addWidget(self._refresh_displays)
        root.addLayout(display_row)

        target_buttons = QHBoxLayout()
        self._apply_target = QPushButton("Apply Screen Target")
        self._apply_target.clicked.connect(self._apply_screen_target)
        target_buttons.addWidget(self._apply_target)
        target_buttons.addWidget(
            QLabel(
                "Use this after changing the target while Beam is already connected."
            )
        )
        target_buttons.addStretch()
        root.addLayout(target_buttons)

        buttons = QHBoxLayout()
        self._connect = QPushButton("Connect Beam")
        self._connect.clicked.connect(self._connect_beam)
        buttons.addWidget(self._connect)
        self._validate = QPushButton("Validate Live Gaze")
        self._validate.clicked.connect(self._validate_beam)
        buttons.addWidget(self._validate)
        self._disconnect = QPushButton("Disconnect")
        self._disconnect.clicked.connect(
            lambda: controller.device_manager.disconnect_device(DeviceType.BEAM)
        )
        buttons.addWidget(self._disconnect)
        buttons.addStretch()
        root.addLayout(buttons)

        self._status = QLabel("● Beam disconnected")
        self._status.setStyleSheet("font-weight: bold; color: #888888;")
        root.addWidget(self._status)
        self._live = QLabel("Gaze: —   Confidence: —   Samples: 0")
        self._live.setWordWrap(True)
        root.addWidget(self._live)
        self._detail = QLabel(
            "Automatic mode follows the actual participant activity window at trial Start."
        )
        self._detail.setWordWrap(True)
        self._detail.setStyleSheet("color: #666;")
        root.addWidget(self._detail)

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumBlockCount(150)
        self._log.setMaximumHeight(110)
        self._log.setPlaceholderText("Beam connection diagnostics appear here…")
        root.addWidget(self._log)

        dm = controller.device_manager
        dm.device_status_changed.connect(self._on_status_changed)
        dm.beam_stats_changed.connect(self._update_stats)
        dm.beam_log_message.connect(self._append_log)
        self._load_displays()
        self._refresh(dm.status(DeviceType.BEAM))
        self._update_stats(dm.beam_stats())

    def _load_displays(self) -> None:
        selected = self._display.currentData()
        self._display.clear()
        physical_displays = self._controller.device_manager.list_beam_displays()
        if physical_displays:
            fallback = next(
                (
                    display
                    for display in physical_displays
                    if int(display["x"]) <= 0 < int(display["x"]) + int(display["width"])
                    and int(display["y"]) <= 0 < int(display["y"]) + int(display["height"])
                ),
                physical_displays[0],
            )
            self._display.addItem(
                "Automatic — follow participant activity window (recommended)",
                (
                    "auto",
                    int(fallback["x"]),
                    int(fallback["y"]),
                    int(fallback["width"]),
                    int(fallback["height"]),
                ),
            )
        for display in physical_displays:
            data = (
                "manual",
                int(display["x"]),
                int(display["y"]),
                int(display["width"]),
                int(display["height"]),
            )
            self._display.addItem(
                f"Manual — {display['name']} — {display['width']}×{display['height']} physical px "
                f"at ({display['x']}, {display['y']})",
                data,
            )
        if not physical_displays:
            primary = QGuiApplication.primaryScreen()
            qt_screens = list(QGuiApplication.screens())
            fallback_screen = primary or (qt_screens[0] if qt_screens else None)
            if fallback_screen is not None:
                geometry = fallback_screen.geometry()
                self._display.addItem(
                    "Automatic — follow participant activity window (Qt fallback)",
                    (
                        "auto",
                        geometry.x(),
                        geometry.y(),
                        geometry.width(),
                        geometry.height(),
                    ),
                )
            for index, screen in enumerate(qt_screens, start=1):
                geometry = screen.geometry()
                data = (
                    "manual",
                    geometry.x(),
                    geometry.y(),
                    geometry.width(),
                    geometry.height(),
                )
                primary_tag = " — Primary" if screen is primary else ""
                self._display.addItem(
                    f"Manual — Display {index}: {screen.name()} — {geometry.width()}×{geometry.height()} "
                    f"at ({geometry.x()}, {geometry.y()}){primary_tag} — Qt fallback",
                    data,
                )
        if selected is not None:
            idx = self._display.findData(selected)
            if idx >= 0:
                self._display.setCurrentIndex(idx)

    def _connect_beam(self) -> None:
        target = self._display.currentData()
        if target is None:
            QMessageBox.warning(self, "No display", "No participant display was detected.")
            return
        mode, x, y, width, height = target
        viewport = (x, y, width, height)
        try:
            self._controller.device_manager.connect_beam(
                *viewport,
                auto_follow_participant_window=(mode == "auto"),
            )
            self._append_log(
                f"Connecting Beam with {'automatic participant-window targeting' if mode == 'auto' else 'manual display targeting'}; "
                f"fallback/selected viewport x={viewport[0]}, y={viewport[1]}, {viewport[2]}x{viewport[3]}."
            )
        except Exception as exc:
            QMessageBox.critical(self, "Beam connection failed", str(exc))

    def _apply_screen_target(self) -> None:
        target = self._display.currentData()
        if target is None:
            QMessageBox.warning(self, "No display", "No screen recording target was detected.")
            return
        stats = self._controller.device_manager.beam_stats()
        if stats.get("recording_trial_id"):
            QMessageBox.warning(
                self,
                "Recording already active",
                "The Beam screen target cannot be changed while screen_gaze.mp4 is recording. "
                "Finish the current run first.",
            )
            return
        mode, x, y, width, height = target
        try:
            self._controller.device_manager.set_beam_capture_target(
                x,
                y,
                width,
                height,
                auto_follow_participant_window=(mode == "auto"),
            )
            QMessageBox.information(
                self,
                "Screen target applied",
                (
                    "Automatic mode is enabled. HINT will switch to the monitor containing "
                    "the participant activity window when the participant presses Start."
                    if mode == "auto"
                    else "Beam screen recording is now locked to the selected display."
                ),
            )
        except Exception as exc:
            QMessageBox.critical(self, "Could not apply screen target", str(exc))

    def _validate_beam(self) -> None:
        ok, message = self._controller.device_manager.check_beam()
        if ok:
            QMessageBox.information(self, "Beam validation passed", message)
        else:
            QMessageBox.warning(self, "Beam validation failed", message)

    def _on_status_changed(self, device_type: DeviceType, status: DeviceStatus) -> None:
        if device_type == DeviceType.BEAM:
            self._refresh(status)

    def _refresh(self, status: DeviceStatus) -> None:
        labels = {
            DeviceStatus.DISCONNECTED: "● Beam disconnected",
            DeviceStatus.CONNECTING: "● Connecting to Beam SDK…",
            DeviceStatus.CONNECTED: "● Beam connected; waiting for gaze data…",
            DeviceStatus.RECEIVING_DATA: "● Beam is receiving live gaze data",
            DeviceStatus.WARNING: "● Beam stream is not currently delivering fresh data",
            DeviceStatus.ERROR: "● Beam connection error",
        }
        self._status.setText(labels[status])
        self._status.setStyleSheet(f"font-weight: bold; color: {_COLORS[status]};")
        connected = status in (
            DeviceStatus.CONNECTED,
            DeviceStatus.RECEIVING_DATA,
            DeviceStatus.WARNING,
        )
        self._connect.setEnabled(not connected and status != DeviceStatus.CONNECTING)
        self._display.setEnabled(status != DeviceStatus.CONNECTING)
        self._refresh_displays.setEnabled(status != DeviceStatus.CONNECTING)
        self._apply_target.setEnabled(status != DeviceStatus.CONNECTING)
        self._validate.setEnabled(connected)
        self._disconnect.setEnabled(status != DeviceStatus.DISCONNECTED)

    def _update_stats(self, stats: dict) -> None:
        x = stats.get("screen_gaze_x_px", "—")
        y = stats.get("screen_gaze_y_px", "—")
        confidence = stats.get("gaze_confidence", "—")
        samples = stats.get("sample_count", 0)
        viewport = stats.get("viewport", (0, 0, 0, 0))
        recording = stats.get("recording_trial_id") or "no"
        video_frames = stats.get("screen_video_frame_count", 0)
        target_mode = stats.get("capture_target_mode", "—")
        target_source = stats.get("capture_target_source", "—")
        self._live.setText(
            f"Gaze: ({x}, {y}) px   Confidence: {confidence}   Samples: {samples:,}<br>"
            f"SDK: {stats.get('sdk_version') or '—'}   Reception: "
            f"{stats.get('reception_status', '—')}   Recording trial: {recording}<br>"
            f"Screen MP4: {'enabled' if stats.get('screen_video_enabled') else 'disabled'}   "
            f"Frames written: {video_frames:,}<br>"
            f"Capture target: {target_mode}   Source: {target_source}"
        )
        self._detail.setText(
            f"Viewport x={viewport[0]}, y={viewport[1]}, {viewport[2]}×{viewport[3]}. "
            + (stats.get("capture_target_message") or "Confirm live gaze immediately before the study.")
        )
        if stats.get("last_error"):
            self._detail.setText(str(stats["last_error"]))
        if stats.get("screen_video_capture_error"):
            self._detail.setText(
                f"Screen MP4 error: {stats['screen_video_capture_error']}"
            )

    def _append_log(self, message: str) -> None:
        self._log.appendPlainText(message)
