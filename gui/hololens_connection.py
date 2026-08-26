"""HoloLens 2 connection workflow and live validation window."""

from __future__ import annotations

import time

from PySide6.QtCore import QSettings, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.application_controller import ApplicationController
from models.enums import DeviceStatus, DeviceType

_STATUS_COLORS = {
    DeviceStatus.DISCONNECTED: "#888888",
    DeviceStatus.CONNECTING: "#d4a017",
    DeviceStatus.CONNECTED: "#1976d2",
    DeviceStatus.RECEIVING_DATA: "#2e7d32",
    DeviceStatus.WARNING: "#d4a017",
    DeviceStatus.ERROR: "#c0392b",
}


class HoloLensValidationWindow(QDialog):
    """Non-modal live camera + eye-gaze verification window."""

    def __init__(self, controller: ApplicationController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._controller = controller
        self._last_pixmap: QPixmap | None = None
        self._smoothed_gaze: tuple[float, float] | None = None
        self.setWindowTitle("HoloLens 2 Connection Validation")
        self.resize(1180, 720)
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)

        root = QVBoxLayout(self)
        header = QLabel(
            "<b>Live validation</b> — this window reads the already-running HoloLens streams. "
            "Closing it does not disconnect the headset or stop the camera/eye-gaze streams."
        )
        header.setWordWrap(True)
        root.addWidget(header)

        body = QHBoxLayout()
        root.addLayout(body, 1)

        camera_box = QGroupBox("HoloLens PV / front camera")
        camera_layout = QVBoxLayout(camera_box)
        self._camera = QLabel("Waiting for the first camera frame…")
        self._camera.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._camera.setMinimumSize(640, 360)
        self._camera.setStyleSheet("background: #111; color: #ddd; border: 1px solid #444;")
        camera_layout.addWidget(self._camera, 1)
        self._camera_meta = QLabel("—")
        self._camera_meta.setWordWrap(True)
        camera_layout.addWidget(self._camera_meta)
        body.addWidget(camera_box, 3)

        eye_box = QGroupBox("Extended Eye Tracking")
        eye_layout = QVBoxLayout(eye_box)
        self._calibration = QLabel("Calibration: —")
        self._calibration.setWordWrap(True)
        eye_layout.addWidget(self._calibration)

        self._gaze_text = QPlainTextEdit()
        self._gaze_text.setReadOnly(True)
        font = QFont("Consolas")
        font.setStyleHint(QFont.StyleHint.Monospace)
        self._gaze_text.setFont(font)
        self._gaze_text.setPlainText("Waiting for eye-gaze packets…")
        eye_layout.addWidget(self._gaze_text, 1)

        note = QLabel(
            "The moving circle on the RGB image is a pose-registered combined-gaze direction marker. "
            "It projects a point 1.5 m along the EET gaze ray using the nearest PV frame pose and "
            "that frame's camera intrinsics. It is intended for connection/calibration validation; "
            "without scene depth it is not an exact physical-surface fixation point."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #666; font-size: 11px;")
        eye_layout.addWidget(note)
        body.addWidget(eye_box, 2)

        footer = QHBoxLayout()
        self._health = QLabel("Checking live stream health…")
        self._health.setWordWrap(True)
        footer.addWidget(self._health, 1)
        close_btn = QPushButton("Close Validation Window")
        close_btn.clicked.connect(self.close)
        footer.addWidget(close_btn)
        root.addLayout(footer)

        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._refresh)
        self._timer.start()
        self._health_timer_counter = 0

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._refresh()
        self._timer.start()

    def closeEvent(self, event) -> None:
        self._timer.stop()
        super().closeEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_pixmap()

    def _apply_pixmap(self) -> None:
        if self._last_pixmap is None:
            return
        self._camera.setPixmap(
            self._last_pixmap.scaled(
                self._camera.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def _refresh(self) -> None:
        dm = self._controller.device_manager
        snapshot = dm.hololens_latest_camera_gaze_snapshot(distance_m=1.5)
        frame = snapshot.get("frame")
        overlay = snapshot.get("gaze_overlay") or {}
        if frame is not None:
            try:
                if len(frame.shape) == 3 and frame.shape[2] == 3:
                    h, w, _ = frame.shape
                    image = QImage(
                        frame.data,
                        w,
                        h,
                        int(frame.strides[0]),
                        QImage.Format.Format_BGR888,
                    ).copy()
                elif len(frame.shape) == 2:
                    h, w = frame.shape
                    image = QImage(
                        frame.data,
                        w,
                        h,
                        int(frame.strides[0]),
                        QImage.Format.Format_Grayscale8,
                    ).copy()
                else:
                    image = QImage()
                if not image.isNull():
                    pixmap = QPixmap.fromImage(image)
                    self._draw_gaze_overlay(pixmap, overlay)
                    self._last_pixmap = pixmap
                    self._apply_pixmap()
            except Exception as exc:
                self._camera.setText(f"Could not render camera frame: {exc}")

        stats = dm.hololens_stats()
        camera_age = stats.get("last_camera_age_s")
        eye_age = stats.get("last_eye_age_s")
        overlay_text = self._overlay_status_text(overlay)
        self._camera_meta.setText(
            f"Configured: {stats.get('camera_resolution', '—')} @ "
            f"{stats.get('camera_fps_configured', '—')} FPS | "
            f"frames received: {int(stats.get('camera_frame_count', 0)):,} | "
            f"last frame: {self._age(camera_age)}<br>"
            f"{overlay_text}"
        )

        eye = stats.get("latest_eye") or {}
        calibration_valid = bool(eye.get("calibration_valid", False))
        if eye:
            if calibration_valid:
                self._calibration.setText("● Eye calibration: VALID")
                self._calibration.setStyleSheet("color: #2e7d32; font-weight: bold;")
            else:
                self._calibration.setText(
                    "● Eye calibration: NOT VALID — run HoloLens Settings → System → Calibration → Eye Calibration"
                )
                self._calibration.setStyleSheet("color: #c0392b; font-weight: bold;")
            self._gaze_text.setPlainText(self._format_eye(eye, eye_age, stats))

        self._health_timer_counter += 1
        if self._health_timer_counter >= 13:
            self._health_timer_counter = 0
            ok, message = dm.check_hololens()
            self._health.setText(("✓ " if ok else "✗ ") + message)
            self._health.setStyleSheet("color: #2e7d32;" if ok else "color: #c0392b;")

    def _draw_gaze_overlay(self, pixmap: QPixmap, overlay: dict) -> None:
        if not overlay.get("valid", False) or not overlay.get("in_frame", False):
            self._smoothed_gaze = None
            return
        pixel = overlay.get("pixel")
        if not pixel:
            self._smoothed_gaze = None
            return

        x, y = float(pixel[0]), float(pixel[1])
        # Light smoothing removes natural eye-tracker jitter while preserving motion.
        if self._smoothed_gaze is None:
            sx, sy = x, y
        else:
            alpha = 0.42
            sx = alpha * x + (1.0 - alpha) * self._smoothed_gaze[0]
            sy = alpha * y + (1.0 - alpha) * self._smoothed_gaze[1]
        self._smoothed_gaze = (sx, sy)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        radius = max(10.0, min(pixmap.width(), pixmap.height()) * 0.018)
        painter.setPen(QPen(QColor("#00e5ff"), 4.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(int(sx - radius), int(sy - radius), int(radius * 2), int(radius * 2))
        painter.setPen(QPen(QColor("white"), 2.0))
        cross = radius * 0.55
        painter.drawLine(int(sx - cross), int(sy), int(sx + cross), int(sy))
        painter.drawLine(int(sx), int(sy - cross), int(sx), int(sy + cross))
        painter.end()

    @staticmethod
    def _overlay_status_text(overlay: dict) -> str:
        if not overlay.get("valid", False):
            return f"Gaze overlay: unavailable — {overlay.get('reason', 'waiting for data')}"
        pixel = overlay.get("pixel") or (0.0, 0.0)
        delta = overlay.get("timestamp_delta_ms")
        delta_text = "—" if delta is None else f"{float(delta):.1f} ms"
        visibility = "visible" if overlay.get("in_frame", False) else "outside camera view"
        return (
            f"Gaze overlay: {visibility} | pixel ({float(pixel[0]):.1f}, {float(pixel[1]):.1f}) | "
            f"assumed distance {float(overlay.get('distance_m', 1.5)):.1f} m | PV/EET Δt {delta_text}"
        )

    @staticmethod
    def _age(value) -> str:
        return "—" if value is None else f"{float(value):.2f} s ago"

    @staticmethod
    def _fmt_vec(vec) -> str:
        if not vec:
            return "—"
        return f"({vec[0]: .5f}, {vec[1]: .5f}, {vec[2]: .5f})"

    @classmethod
    def _format_eye(cls, eye: dict, eye_age, stats: dict) -> str:
        combined = eye.get("combined") or {}
        left = eye.get("left") or {}
        right = eye.get("right") or {}
        return (
            f"HoloLens timestamp : {eye.get('timestamp', '—')}\n"
            f"Packets received   : {int(stats.get('eye_packet_count', 0)):,}\n"
            f"Configured rate    : {stats.get('eye_fps_configured', '—')} Hz\n"
            f"Last packet        : {cls._age(eye_age)}\n\n"
            f"COMBINED RAY\n"
            f"  valid            : {bool(eye.get('combined_valid', False))}\n"
            f"  origin (m)       : {cls._fmt_vec(combined.get('origin'))}\n"
            f"  direction        : {cls._fmt_vec(combined.get('direction'))}\n\n"
            f"LEFT EYE RAY\n"
            f"  valid            : {bool(eye.get('left_valid', False))}\n"
            f"  origin (m)       : {cls._fmt_vec(left.get('origin'))}\n"
            f"  direction        : {cls._fmt_vec(left.get('direction'))}\n\n"
            f"RIGHT EYE RAY\n"
            f"  valid            : {bool(eye.get('right_valid', False))}\n"
            f"  origin (m)       : {cls._fmt_vec(right.get('origin'))}\n"
            f"  direction        : {cls._fmt_vec(right.get('direction'))}\n\n"
            "Eye openness / vergence are omitted from the main validation readout because "
            "Microsoft documents those fields as unsupported on HoloLens 2."
        )


class HoloLensConnectionPanel(QGroupBox):
    """Detailed HL2SS connection workflow for Microsoft HoloLens 2."""

    SETTINGS_ORG = "HINTResearch"
    SETTINGS_APP = "HINT Study Console"

    def __init__(self, controller: ApplicationController, parent: QWidget | None = None) -> None:
        super().__init__("Microsoft HoloLens 2 — Eye Gaze + PV Camera", parent)
        self._controller = controller
        self._settings = QSettings(self.SETTINGS_ORG, self.SETTINGS_APP)
        self._validation_window: HoloLensValidationWindow | None = None
        self._auto_validation_shown = False

        root = QVBoxLayout(self)
        root.setSpacing(10)

        intro = QLabel(
            "This connection uses <b>HL2SS</b> to receive the HoloLens 2 front/PV RGB camera "
            "and <b>Extended Eye Tracking</b> over the local network. The streams remain active "
            "after connection. A separate validation window opens automatically once per successful "
            "connection and can be reopened later with <b>Validate Connection</b>."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        steps = QLabel(
            "<b>One-time HoloLens / PC setup</b><br>"
            "1. Update HoloLens 2 and connect the headset and study PC to the <b>same Wi-Fi/LAN</b>.<br>"
            "2. On HoloLens: <b>Settings → Update &amp; Security → For developers</b>. Enable "
            "<b>Developer Mode</b> and <b>Device Portal</b>.<br>"
            "3. Find the headset IPv4 address in <b>Settings → Network &amp; Internet → Wi-Fi → "
            "Advanced options / Hardware properties</b> (or ask HoloLens “What's my IP address?”).<br>"
            "4. Download the current <b>HL2SS</b> repository on this PC. Keep its <b>viewer</b> folder; "
            "the console imports the official Python client from there.<br>"
            "5. Install the latest HL2SS <b>.appxbundle</b> on the headset, either directly or through "
            "Windows Device Portal → Views → Apps. The app is then available in <b>All apps</b>.<br>"
            "6. Put the participant/user on the headset and run <b>Settings → System → Calibration → "
            "Eye Calibration → Run eye calibration</b>.<br>"
            "7. Launch the <b>hl2ss</b> app on HoloLens. On first launch, allow <b>Camera</b>, "
            "<b>Eye tracker</b>, <b>Microphone</b>, and <b>User movements</b> permissions when requested.<br>"
            "8. In this console, enter the HoloLens IP, choose the downloaded HL2SS repository/root or "
            "<b>viewer</b> folder, choose the eye-tracking rate, and click <b>Connect HoloLens</b>.<br>"
            "9. Wait until the status becomes green: <b>receiving live PV camera + eye-gaze data</b>. "
            "The validation window opens automatically. Confirm that the camera moves live and that "
            "the combined/left/right gaze vectors update while the participant looks around.<br>"
            "10. Close the validation window when done. During the session, use <b>Validate Connection</b> "
            "any time to reopen the same live camera + gaze view.<br><br>"
            "<b>Research Mode note:</b> it is not required for the two streams used by this console "
            "(PV camera + Extended Eye Tracking). Enable it only if you later add raw Research Mode "
            "VLC/depth/IMU streams; it increases battery use."
        )
        steps.setWordWrap(True)
        steps.setStyleSheet(
            "QLabel { background: #f5f7fa; border: 1px solid #d9dee5; "
            "border-radius: 5px; padding: 10px; }"
        )
        root.addWidget(steps)

        links = QHBoxLayout()
        hl2ss_btn = QPushButton("Open HL2SS Repository / Releases")
        hl2ss_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl("https://github.com/jdibenes/hl2ss"))
        )
        links.addWidget(hl2ss_btn)
        self._portal_btn = QPushButton("Open HoloLens Device Portal")
        self._portal_btn.clicked.connect(self._open_device_portal)
        links.addWidget(self._portal_btn)
        links.addStretch()
        root.addLayout(links)

        form_box = QGroupBox("Connection settings")
        form = QFormLayout(form_box)
        self._host = QLineEdit(str(self._settings.value("hololens/host", "192.168.1.7")))
        self._host.setPlaceholderText("e.g. 192.168.1.7")
        form.addRow("HoloLens IPv4 address:", self._host)

        client_row = QHBoxLayout()
        self._client_dir = QLineEdit(str(self._settings.value("hololens/client_dir", "")))
        self._client_dir.setPlaceholderText("Select HL2SS repository root or viewer folder")
        client_row.addWidget(self._client_dir, 1)
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse_client_dir)
        client_row.addWidget(browse_btn)
        form.addRow("HL2SS client folder:", client_row)

        self._eye_fps = QComboBox()
        for fps in (30, 60, 90):
            self._eye_fps.addItem(f"{fps} Hz", fps)
        saved_eye_fps = int(self._settings.value("hololens/eye_fps", 60))
        index = self._eye_fps.findData(saved_eye_fps)
        self._eye_fps.setCurrentIndex(max(0, index))
        form.addRow("Extended eye tracking rate:", self._eye_fps)

        self._camera_mode = QComboBox()
        self._camera_mode.addItem("1280 × 720 @ 30 FPS (recommended)", (1280, 720, 30))
        self._camera_mode.addItem("760 × 428 @ 30 FPS (lower bandwidth)", (760, 428, 30))
        self._camera_mode.addItem("1920 × 1080 @ 30 FPS (higher bandwidth)", (1920, 1080, 30))
        form.addRow("PV camera validation stream:", self._camera_mode)
        root.addWidget(form_box)

        buttons = QHBoxLayout()
        self._connect_btn = QPushButton("Connect HoloLens")
        self._connect_btn.clicked.connect(self._connect)
        buttons.addWidget(self._connect_btn)
        self._validate_btn = QPushButton("Validate Connection")
        self._validate_btn.clicked.connect(self._validate)
        buttons.addWidget(self._validate_btn)
        self._disconnect_btn = QPushButton("Disconnect")
        self._disconnect_btn.clicked.connect(
            lambda: controller.device_manager.disconnect_device(DeviceType.HOLOLENS)
        )
        buttons.addWidget(self._disconnect_btn)
        buttons.addStretch()
        root.addLayout(buttons)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        root.addWidget(self._progress)
        self._progress_text = QLabel("Not connected")
        self._progress_text.setWordWrap(True)
        root.addWidget(self._progress_text)

        self._status = QLabel("● HoloLens disconnected")
        self._status.setWordWrap(True)
        root.addWidget(self._status)

        live_box = QGroupBox("Live stream status")
        grid = QGridLayout(live_box)
        self._host_value = QLabel("—")
        self._camera_value = QLabel("—")
        self._eye_value = QLabel("—")
        self._calibration_value = QLabel("—")
        self._health_value = QLabel("Connect first; then the live data health appears here.")
        self._health_value.setWordWrap(True)
        rows = [
            ("Host", self._host_value),
            ("PV camera", self._camera_value),
            ("Eye tracking", self._eye_value),
            ("Eye calibration", self._calibration_value),
            ("Verification", self._health_value),
        ]
        for row, (name, widget) in enumerate(rows):
            grid.addWidget(QLabel(f"<b>{name}:</b>"), row, 0)
            grid.addWidget(widget, row, 1)
        grid.setColumnStretch(1, 1)
        root.addWidget(live_box)

        log_box = QGroupBox("HoloLens connection log")
        log_layout = QVBoxLayout(log_box)
        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumBlockCount(250)
        self._log.setMinimumHeight(125)
        self._log.setPlaceholderText("HoloLens connection diagnostics appear here…")
        log_layout.addWidget(self._log)
        root.addWidget(log_box)

        dm = controller.device_manager
        dm.device_status_changed.connect(self._on_status_changed)
        dm.hololens_connection_progress.connect(self._on_progress)
        dm.hololens_log_message.connect(self._append_log)
        dm.hololens_stream_stats_changed.connect(self._update_stats)

        self._stats_timer = QTimer(self)
        self._stats_timer.setInterval(500)
        self._stats_timer.timeout.connect(lambda: self._update_stats(dm.hololens_stats()))
        self._stats_timer.start()

        self._refresh_status(dm.status(DeviceType.HOLOLENS))
        self._update_stats(dm.hololens_stats())

    # ------------------------------------------------------------------

    def _browse_client_dir(self) -> None:
        current = self._client_dir.text().strip()
        selected = QFileDialog.getExistingDirectory(
            self,
            "Select HL2SS repository root or viewer folder",
            current,
        )
        if selected:
            self._client_dir.setText(selected)
            if not self._controller.device_manager.hololens_client_dir_valid(selected):
                QMessageBox.warning(
                    self,
                    "HL2SS Folder",
                    "That folder does not contain the HL2SS Python client. Select either the "
                    "HL2SS repository root or its viewer folder (containing hl2ss.py and hl2ss_lnm.py).",
                )

    def _open_device_portal(self) -> None:
        host = self._host.text().strip()
        if not host:
            QMessageBox.information(self, "HoloLens Device Portal", "Enter the HoloLens IP address first.")
            return
        QDesktopServices.openUrl(QUrl(f"https://{host}"))

    def _connect(self) -> None:
        host = self._host.text().strip()
        client_dir = self._client_dir.text().strip()
        if not self._controller.device_manager.hololens_client_dir_valid(client_dir):
            QMessageBox.warning(
                self,
                "HL2SS Client Folder Required",
                "Select the downloaded HL2SS repository root or its viewer folder before connecting.",
            )
            return

        width, height, camera_fps = self._camera_mode.currentData()
        eye_fps = int(self._eye_fps.currentData())
        self._settings.setValue("hololens/host", host)
        self._settings.setValue("hololens/client_dir", client_dir)
        self._settings.setValue("hololens/eye_fps", eye_fps)

        self._auto_validation_shown = False
        self._log.clear()
        self._health_value.setText("Connecting — waiting for both live streams…")
        self._append_log(f"Starting HoloLens connection to {host}.")
        try:
            self._controller.device_manager.connect_hololens(
                host,
                client_dir,
                eye_fps=eye_fps,
                width=int(width),
                height=int(height),
                camera_fps=int(camera_fps),
            )
        except Exception as exc:
            QMessageBox.critical(self, "HoloLens Connection Failed", str(exc))
            self._health_value.setText(f"Connection setup failed: {exc}")

    def _validate(self) -> None:
        ok, message = self._controller.device_manager.check_hololens()
        self._health_value.setText(("✓ " if ok else "✗ ") + message)
        if not ok:
            QMessageBox.warning(self, "HoloLens Validation", message)
            return
        self._show_validation_window()

    def _show_validation_window(self) -> None:
        if self._validation_window is None:
            self._validation_window = HoloLensValidationWindow(self._controller, self)
        self._validation_window.show()
        self._validation_window.raise_()
        self._validation_window.activateWindow()

    def _on_progress(self, percent: int, text: str) -> None:
        self._progress.setValue(max(0, min(100, int(percent))))
        self._progress_text.setText(text)

    def _append_log(self, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self._log.appendPlainText(f"[{stamp}] {message}")

    def _on_status_changed(self, device_type: DeviceType, status: DeviceStatus) -> None:
        if device_type != DeviceType.HOLOLENS:
            return
        self._refresh_status(status)
        self._update_stats(self._controller.device_manager.hololens_stats())
        if status == DeviceStatus.RECEIVING_DATA and not self._auto_validation_shown:
            self._auto_validation_shown = True
            self._health_value.setText(
                "✓ Both HoloLens streams are live. Opening the one-time post-connect validation window…"
            )
            QTimer.singleShot(150, self._show_validation_window)
        elif status == DeviceStatus.DISCONNECTED:
            self._auto_validation_shown = False
            if self._validation_window is not None:
                self._validation_window.close()

    def _refresh_status(self, status: DeviceStatus) -> None:
        color = _STATUS_COLORS.get(status, "#888888")
        messages = {
            DeviceStatus.DISCONNECTED: "● HoloLens disconnected",
            DeviceStatus.CONNECTING: "● Connecting to HoloLens / HL2SS…",
            DeviceStatus.CONNECTED: "● One HoloLens stream is live; waiting for the other…",
            DeviceStatus.RECEIVING_DATA: "● Connected and receiving live PV camera + eye-gaze data",
            DeviceStatus.WARNING: "● HoloLens stream warning — validate/reconnect before collection",
            DeviceStatus.ERROR: "● HoloLens connection failed — review the log below",
        }
        self._status.setText(messages.get(status, f"● {status.value}"))
        self._status.setStyleSheet(
            "QLabel { "
            f"color: {color}; background: #f6f6f6; border: 1px solid {color}; "
            "border-radius: 5px; padding: 9px; font-weight: bold; }"
        )
        connected = status in (DeviceStatus.CONNECTED, DeviceStatus.RECEIVING_DATA, DeviceStatus.WARNING)
        busy = status == DeviceStatus.CONNECTING
        self._connect_btn.setEnabled(not connected and not busy)
        self._host.setEnabled(not connected and not busy)
        self._client_dir.setEnabled(not connected and not busy)
        self._eye_fps.setEnabled(not connected and not busy)
        self._camera_mode.setEnabled(not connected and not busy)
        self._validate_btn.setEnabled(status in (DeviceStatus.RECEIVING_DATA, DeviceStatus.WARNING))
        self._disconnect_btn.setEnabled(status != DeviceStatus.DISCONNECTED)
        if status == DeviceStatus.DISCONNECTED:
            self._progress.setValue(0)
            self._progress_text.setText("Not connected")
            self._health_value.setText("Connect first; then the live data health appears here.")
        elif status == DeviceStatus.ERROR:
            stats = self._controller.device_manager.hololens_stats()
            self._health_value.setText(stats.get("last_error") or "HoloLens connection error.")

    def _update_stats(self, stats: dict) -> None:
        self._host_value.setText(stats.get("host") or "—")
        camera_age = stats.get("last_camera_age_s")
        eye_age = stats.get("last_eye_age_s")
        self._camera_value.setText(
            f"{stats.get('camera_resolution', '—')} @ {stats.get('camera_fps_configured', '—')} FPS | "
            f"{int(stats.get('camera_frame_count', 0)):,} frames | last: {self._age(camera_age)}"
        )
        self._eye_value.setText(
            f"{stats.get('eye_fps_configured', '—')} Hz | "
            f"{int(stats.get('eye_packet_count', 0)):,} packets | last: {self._age(eye_age)}"
        )
        eye = stats.get("latest_eye") or {}
        if not eye:
            self._calibration_value.setText("—")
        elif eye.get("calibration_valid"):
            self._calibration_value.setText("✓ Valid")
            self._calibration_value.setStyleSheet("color: #2e7d32; font-weight: bold;")
        else:
            self._calibration_value.setText("✗ Not valid — recalibrate participant on HoloLens")
            self._calibration_value.setStyleSheet("color: #c0392b; font-weight: bold;")

    def _draw_gaze_overlay(self, pixmap: QPixmap, overlay: dict) -> None:
        if not overlay.get("valid", False) or not overlay.get("in_frame", False):
            self._smoothed_gaze = None
            return
        pixel = overlay.get("pixel")
        if not pixel:
            self._smoothed_gaze = None
            return

        x, y = float(pixel[0]), float(pixel[1])
        # Light smoothing removes natural eye-tracker jitter while preserving motion.
        if self._smoothed_gaze is None:
            sx, sy = x, y
        else:
            alpha = 0.42
            sx = alpha * x + (1.0 - alpha) * self._smoothed_gaze[0]
            sy = alpha * y + (1.0 - alpha) * self._smoothed_gaze[1]
        self._smoothed_gaze = (sx, sy)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        radius = max(10.0, min(pixmap.width(), pixmap.height()) * 0.018)
        painter.setPen(QPen(QColor("#00e5ff"), 4.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(int(sx - radius), int(sy - radius), int(radius * 2), int(radius * 2))
        painter.setPen(QPen(QColor("white"), 2.0))
        cross = radius * 0.55
        painter.drawLine(int(sx - cross), int(sy), int(sx + cross), int(sy))
        painter.drawLine(int(sx), int(sy - cross), int(sx), int(sy + cross))
        painter.end()

    @staticmethod
    def _overlay_status_text(overlay: dict) -> str:
        if not overlay.get("valid", False):
            return f"Gaze overlay: unavailable — {overlay.get('reason', 'waiting for data')}"
        pixel = overlay.get("pixel") or (0.0, 0.0)
        delta = overlay.get("timestamp_delta_ms")
        delta_text = "—" if delta is None else f"{float(delta):.1f} ms"
        visibility = "visible" if overlay.get("in_frame", False) else "outside camera view"
        return (
            f"Gaze overlay: {visibility} | pixel ({float(pixel[0]):.1f}, {float(pixel[1]):.1f}) | "
            f"assumed distance {float(overlay.get('distance_m', 1.5)):.1f} m | PV/EET Δt {delta_text}"
        )

    @staticmethod
    def _age(value) -> str:
        return "—" if value is None else f"{float(value):.2f} s ago"
