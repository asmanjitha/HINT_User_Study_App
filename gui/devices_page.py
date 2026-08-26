"""Study device connection page.

Shimmer has a dedicated guided workflow because it is now a real hardware
integration: select the paired Bluetooth COM port, watch handshake/config
progress, confirm live GSR+PPG samples, and re-check stream health at any time.
Keyboard, joystick/gamepad, and microphone also have selectable real-device panels;
HoloLens 2 now uses HL2SS for live PV camera + Extended Eye Tracking validation.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core.application_controller import ApplicationController
from gui.hololens_connection import HoloLensConnectionPanel
from models.enums import DeviceStatus, DeviceType

_STATUS_COLORS = {
    DeviceStatus.DISCONNECTED: "#888888",
    DeviceStatus.CONNECTING: "#d4a017",
    DeviceStatus.CONNECTED: "#1976d2",
    DeviceStatus.RECEIVING_DATA: "#2e7d32",
    DeviceStatus.WARNING: "#d4a017",
    DeviceStatus.ERROR: "#c0392b",
}

_DEVICE_HINTS = {
    DeviceType.HOLOLENS: "Implicit feedback: gaze, hand joints, first-person view",
    DeviceType.JOYSTICK: "Explicit feedback input",
    DeviceType.KEYBOARD: "Explicit feedback input",
    DeviceType.MICROPHONE: "Explicit feedback: voice commands",
}


class ShimmerConnectionPanel(QGroupBox):
    VERIFY_WINDOW_MS = 1500

    def __init__(self, controller: ApplicationController, parent: QWidget | None = None) -> None:
        super().__init__("Shimmer GSR + PPG Connection", parent)
        self._controller = controller
        self._verify_start_count = 0
        self._verify_start_time = 0.0
        self._success_notified = False

        root = QVBoxLayout(self)
        root.setSpacing(10)

        intro = QLabel(
            "Use this guided setup before starting participant data collection. "
            "The console configures the Shimmer for <b>GSR + optical PPG at 128 Hz</b>, "
            "starts the realtime stream, and keeps a connection-level diagnostic log. "
            "During experimental Study 1/2 trials, GSR + PPG samples are also saved "
            "automatically inside that trial's sensors folder."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        steps = QLabel(
            "<b>Connection steps</b><br>"
            "1. Connect the GSR electrodes and optical PPG probe to the Shimmer GSR+ unit.<br>"
            "2. Power on the Shimmer.<br>"
            "3. In <b>Windows Settings → Bluetooth &amp; devices</b>, pair/connect the Shimmer. "
            "Windows must create a Bluetooth virtual COM port for it.<br>"
            "4. Close Consensys/LogAndStream or any other program that may already be using that COM port.<br>"
            "5. Return here and click <b>Refresh Ports</b>. If needed, use Windows Device Manager → Ports (COM &amp; LPT) "
            "to identify the Shimmer COM number.<br>"
            "6. Select that COM port and click <b>Connect &amp; Start Streaming</b>.<br>"
            "7. Follow the progress bar. Continue only after the green <b>Connected and receiving live GSR + PPG data</b> confirmation appears.<br>"
            "8. Before (or during) a study run, click <b>Check Live Data</b> to verify that new samples are still reaching this GUI.<br>"
            "9. When an experimental Study 1/2 run starts, confirm the <b>Study recording</b> row below changes to Recording. "
            "Training/practice runs are intentionally not included in the primary Shimmer study CSVs."
        )
        steps.setWordWrap(True)
        steps.setTextInteractionFlags(steps.textInteractionFlags())
        steps.setStyleSheet(
            "QLabel { background: #f5f7fa; border: 1px solid #d9dee5; "
            "border-radius: 5px; padding: 10px; }"
        )
        root.addWidget(steps)

        port_row = QHBoxLayout()
        port_row.addWidget(QLabel("Bluetooth COM port:"))
        self._port_combo = QComboBox()
        self._port_combo.setMinimumWidth(360)
        port_row.addWidget(self._port_combo, 1)

        self._refresh_ports_btn = QPushButton("Refresh Ports")
        self._refresh_ports_btn.clicked.connect(self._refresh_ports)
        port_row.addWidget(self._refresh_ports_btn)

        self._connect_btn = QPushButton("Connect && Start Streaming")
        self._connect_btn.clicked.connect(self._connect)
        port_row.addWidget(self._connect_btn)

        self._disconnect_btn = QPushButton("Disconnect")
        self._disconnect_btn.clicked.connect(
            lambda: controller.device_manager.disconnect_device(DeviceType.SHIMMER)
        )
        port_row.addWidget(self._disconnect_btn)
        root.addLayout(port_row)

        self._serial_warning = QLabel("")
        self._serial_warning.setWordWrap(True)
        self._serial_warning.setStyleSheet("color: #c0392b;")
        root.addWidget(self._serial_warning)

        progress_box = QGroupBox("Connection progress")
        progress_layout = QVBoxLayout(progress_box)
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        progress_layout.addWidget(self._progress)

        self._progress_text = QLabel("Not connected")
        self._progress_text.setWordWrap(True)
        progress_layout.addWidget(self._progress_text)

        self._status_banner = QLabel("● Shimmer disconnected")
        self._status_banner.setWordWrap(True)
        self._status_banner.setStyleSheet(
            "QLabel { color: #666; background: #f6f6f6; border-radius: 5px; padding: 9px; "
            "font-weight: bold; }"
        )
        progress_layout.addWidget(self._status_banner)
        root.addWidget(progress_box)

        live_box = QGroupBox("Live connection and data")
        live_grid = QGridLayout(live_box)
        self._port_value = QLabel("—")
        self._hw_value = QLabel("—")
        self._fw_value = QLabel("—")
        self._rate_value = QLabel("—")
        self._samples_value = QLabel("0")
        self._last_data_value = QLabel("—")
        self._gsr_value = QLabel("—")
        self._ppg_value = QLabel("—")
        self._csv_value = QLabel("—")
        self._csv_value.setWordWrap(True)
        self._csv_value.setTextInteractionFlags(self._csv_value.textInteractionFlags())
        self._study_recording_value = QLabel("Inactive — starts automatically with an experimental trial")
        self._study_recording_value.setWordWrap(True)
        self._study_csv_value = QLabel("—")
        self._study_csv_value.setWordWrap(True)

        rows = [
            ("Port", self._port_value),
            ("Hardware", self._hw_value),
            ("Firmware", self._fw_value),
            ("Configured sample rate", self._rate_value),
            ("Samples received", self._samples_value),
            ("Last data received", self._last_data_value),
            ("Latest GSR", self._gsr_value),
            ("Latest PPG", self._ppg_value),
            ("Connection diagnostic CSV", self._csv_value),
            ("Study recording", self._study_recording_value),
            ("Study GSR/PPG CSV", self._study_csv_value),
        ]
        for row, (name, widget) in enumerate(rows):
            label = QLabel(f"<b>{name}:</b>")
            label.setAlignment(label.alignment())
            live_grid.addWidget(label, row, 0)
            live_grid.addWidget(widget, row, 1)
        live_grid.setColumnStretch(1, 1)

        verify_row = QHBoxLayout()
        self._verify_btn = QPushButton("Check Live Data")
        self._verify_btn.setToolTip(
            "Checks whether new Shimmer samples are still arriving at the GUI, not just whether the COM port is open."
        )
        self._verify_btn.clicked.connect(self._start_verification)
        verify_row.addWidget(self._verify_btn)
        self._verify_result = QLabel("Connect first, then use this button to verify the stream at any time.")
        self._verify_result.setWordWrap(True)
        verify_row.addWidget(self._verify_result, 1)
        live_grid.addLayout(verify_row, len(rows), 0, 1, 2)
        root.addWidget(live_box)

        log_box = QGroupBox("Shimmer connection log")
        log_layout = QVBoxLayout(log_box)
        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumBlockCount(250)
        self._log.setMinimumHeight(145)
        self._log.setPlaceholderText("Connection progress and Shimmer messages appear here...")
        log_layout.addWidget(self._log)
        root.addWidget(log_box)

        dm = controller.device_manager
        dm.device_status_changed.connect(self._on_status_changed)
        dm.shimmer_connection_progress.connect(self._on_progress)
        dm.shimmer_log_message.connect(self._append_log)
        dm.shimmer_stream_stats_changed.connect(self._update_stats)

        # This timer keeps "last packet age" visibly current even when stats
        # signals are throttled and also catches a stream that has gone stale.
        self._health_timer = QTimer(self)
        self._health_timer.setInterval(500)
        self._health_timer.timeout.connect(self._refresh_live_age)
        self._health_timer.start()

        self._refresh_ports()
        self._refresh_status(dm.status(DeviceType.SHIMMER))
        self._update_stats(dm.shimmer_stats())

    # ------------------------------------------------------------------

    def _refresh_ports(self) -> None:
        current = self._port_combo.currentData()
        self._port_combo.clear()
        dm = self._controller.device_manager
        if not dm.shimmer_serial_available():
            self._serial_warning.setText(
                "pyserial is not installed. Install the updated requirements before connecting Shimmer: "
                "pip install -r requirements.txt"
            )
            self._port_combo.addItem("pyserial not installed", "")
            self._connect_btn.setEnabled(False)
            return

        self._serial_warning.clear()
        ports = dm.list_shimmer_ports()
        if not ports:
            self._port_combo.addItem("No serial ports detected — pair Shimmer in Windows first", "")
            self._connect_btn.setEnabled(False)
            return

        selected_index = 0
        for index, port in enumerate(ports):
            device = str(port["device"])
            desc = str(port["description"] or "Serial Port")
            likely = bool(port["likely_bluetooth"])
            tag = "  ← Bluetooth candidate" if likely else ""
            self._port_combo.addItem(f"{device} — {desc}{tag}", device)
            if current and device == current:
                selected_index = index
        self._port_combo.setCurrentIndex(selected_index)
        status = dm.status(DeviceType.SHIMMER)
        self._connect_btn.setEnabled(
            status not in (DeviceStatus.CONNECTING, DeviceStatus.CONNECTED, DeviceStatus.RECEIVING_DATA)
        )
        self._append_log(f"Found {len(ports)} serial port(s). Select the Shimmer Bluetooth COM port.")

    def _connect(self) -> None:
        port = self._port_combo.currentData()
        if not port:
            self._verify_result.setText("Select a valid Bluetooth COM port before connecting.")
            return
        self._success_notified = False
        self._verify_result.setText("Waiting for a successful realtime stream...")
        self._log.clear()
        self._append_log(f"Starting Shimmer connection on {port}.")
        self._controller.device_manager.connect_shimmer(str(port))

    def _on_progress(self, percent: int, text: str) -> None:
        self._progress.setValue(max(0, min(100, percent)))
        self._progress_text.setText(text)

    def _append_log(self, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self._log.appendPlainText(f"[{stamp}] {message}")

    def _on_status_changed(self, device_type: DeviceType, status: DeviceStatus) -> None:
        if device_type != DeviceType.SHIMMER:
            return
        self._refresh_status(status)
        self._update_stats(self._controller.device_manager.shimmer_stats())

    def _refresh_status(self, status: DeviceStatus) -> None:
        color = _STATUS_COLORS.get(status, "#888888")
        messages = {
            DeviceStatus.DISCONNECTED: "● Shimmer disconnected",
            DeviceStatus.CONNECTING: "● Connecting and configuring Shimmer...",
            DeviceStatus.CONNECTED: "● Shimmer identified; waiting for live sensor data...",
            DeviceStatus.RECEIVING_DATA: "● Connected and receiving live GSR + PPG data",
            DeviceStatus.WARNING: "● Shimmer connection warning",
            DeviceStatus.ERROR: "● Shimmer connection failed — review the log below",
        }
        self._status_banner.setText(messages.get(status, status.value))
        self._status_banner.setStyleSheet(
            "QLabel { "
            f"color: {color}; background: #f6f6f6; border: 1px solid {color}; "
            "border-radius: 5px; padding: 9px; font-weight: bold; }"
        )
        connected = status in (
            DeviceStatus.CONNECTED, DeviceStatus.RECEIVING_DATA, DeviceStatus.WARNING
        )
        busy = status == DeviceStatus.CONNECTING
        has_port = bool(self._port_combo.currentData())
        self._connect_btn.setEnabled(not connected and not busy and has_port)
        self._refresh_ports_btn.setEnabled(not busy and not connected)
        self._port_combo.setEnabled(not busy and not connected)
        self._disconnect_btn.setEnabled(status != DeviceStatus.DISCONNECTED)
        self._verify_btn.setEnabled(status in (DeviceStatus.RECEIVING_DATA, DeviceStatus.WARNING))
        if status == DeviceStatus.DISCONNECTED:
            self._progress.setValue(0)
            self._progress_text.setText("Not connected")
            self._verify_result.setText("Connect first, then use this button to verify the stream at any time.")
            self._success_notified = False
        elif status == DeviceStatus.RECEIVING_DATA and not self._success_notified:
            self._success_notified = True
            stats = self._controller.device_manager.shimmer_stats()
            log_path = stats.get("log_path") or "the Shimmer device log folder"
            self._verify_result.setText(
                "✓ Connection confirmed: live GSR + PPG samples are reaching the GUI. "
                "Use Check Live Data at any time to re-verify the stream."
            )
            QMessageBox.information(
                self,
                "Shimmer Connected",
                "Shimmer connected successfully.\n\n"
                "Live GSR + PPG samples are reaching the GUI. A connection diagnostic "
                "CSV is being logged continuously.\n\n"
                "When an experimental Study 1/2 trial starts, the console will automatically "
                "save a separate GSR/PPG CSV inside that trial's sensors folder.\n\n"
                f"Diagnostic CSV: {log_path}",
            )
        elif status == DeviceStatus.ERROR:
            stats = self._controller.device_manager.shimmer_stats()
            if stats.get("last_error"):
                self._verify_result.setText(f"Connection error: {stats['last_error']}")

    def _update_stats(self, stats: dict) -> None:
        self._port_value.setText(stats.get("port") or "—")
        hw = stats.get("hardware_version")
        self._hw_value.setText("—" if hw is None else f"Shimmer hardware version {hw}")
        self._fw_value.setText(stats.get("firmware") or "—")
        rate = stats.get("sampling_rate_hz")
        self._rate_value.setText("—" if not rate else f"{float(rate):.2f} Hz")
        self._samples_value.setText(
            f"{int(stats.get('sample_count', 0)):,}  "
            f"(~{float(stats.get('packet_rate_hz', 0.0)):.1f} samples/s since stream start)"
        )
        age = stats.get("last_packet_age_s")
        self._last_data_value.setText("—" if age is None else f"{float(age):.2f} s ago")
        latest = stats.get("latest") or {}
        if "gsr_raw" in latest:
            self._gsr_value.setText(
                f"raw={latest.get('gsr_raw')}   ADC={latest.get('gsr_adc')}   range={latest.get('gsr_range')}"
            )
        else:
            self._gsr_value.setText("—")
        self._ppg_value.setText(
            "—" if "ppg_raw" not in latest else f"raw={latest.get('ppg_raw')}"
        )
        self._csv_value.setText(stats.get("log_path") or "—")
        if stats.get("study_recording_active"):
            self._study_recording_value.setText(
                f"● Recording {stats.get('study_recording_trial_id')} — "
                f"{int(stats.get('study_recording_sample_count', 0)):,} samples saved "
                f"({float(stats.get('study_recording_elapsed_s', 0.0)):.1f} s)"
            )
            self._study_recording_value.setStyleSheet("color: #2e7d32; font-weight: bold;")
            self._study_csv_value.setText(stats.get("study_recording_path") or "—")
        else:
            self._study_recording_value.setText(
                "Inactive — starts automatically with an experimental Study 1/2 trial"
            )
            self._study_recording_value.setStyleSheet("color: #666;")
            self._study_csv_value.setText("—")

    def _refresh_live_age(self) -> None:
        stats = self._controller.device_manager.shimmer_stats()
        self._update_stats(stats)
        status = self._controller.device_manager.status(DeviceType.SHIMMER)
        age = stats.get("last_packet_age_s")
        if status in (DeviceStatus.RECEIVING_DATA, DeviceStatus.WARNING) and age is not None and float(age) > 2.0:
            self._verify_result.setText(
                f"⚠ No new Shimmer data for {float(age):.1f} s. Click Check Live Data or reconnect."
            )

    # ------------------------------------------------------------------
    # Explicit live-data verification requested for the study workflow.

    def _start_verification(self) -> None:
        stats = self._controller.device_manager.shimmer_stats()
        if self._controller.device_manager.status(DeviceType.SHIMMER) not in (
            DeviceStatus.RECEIVING_DATA, DeviceStatus.WARNING
        ):
            self._verify_result.setText("✗ Shimmer is not currently streaming data to the GUI.")
            return
        self._verify_start_count = int(stats.get("sample_count", 0))
        self._verify_start_time = time.monotonic()
        self._verify_btn.setEnabled(False)
        self._verify_result.setText("Checking for new realtime samples for 1.5 seconds...")
        QTimer.singleShot(self.VERIFY_WINDOW_MS, self._finish_verification)

    def _finish_verification(self) -> None:
        stats = self._controller.device_manager.shimmer_stats()
        current = int(stats.get("sample_count", 0))
        delta = current - self._verify_start_count
        elapsed = max(0.001, time.monotonic() - self._verify_start_time)
        age = stats.get("last_packet_age_s")
        healthy = (
            self._controller.device_manager.status(DeviceType.SHIMMER) in (
                DeviceStatus.RECEIVING_DATA, DeviceStatus.WARNING
            )
            and delta > 0
            and age is not None
            and float(age) < 1.0
        )
        if healthy:
            self._verify_result.setText(
                f"✓ Live stream verified: {delta} new samples received in {elapsed:.1f} s "
                f"(~{delta / elapsed:.1f} samples/s); last packet {float(age):.2f} s ago."
            )
        else:
            age_text = "no packet received" if age is None else f"last packet {float(age):.2f} s ago"
            self._verify_result.setText(
                f"✗ Live stream check failed: {delta} new samples in {elapsed:.1f} s; {age_text}. "
                "Reconnect Shimmer before starting/continuing data collection."
            )
        self._verify_btn.setEnabled(
            self._controller.device_manager.status(DeviceType.SHIMMER) in (
                DeviceStatus.RECEIVING_DATA, DeviceStatus.WARNING
            )
        )



class _SelectableInputPanel(QGroupBox):
    """Shared status/button behavior for one selectable input device."""

    def __init__(
        self,
        title: str,
        controller: ApplicationController,
        device_type: DeviceType,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(title, parent)
        self._controller = controller
        self._device_type = device_type

    def _status_banner(self) -> QLabel:
        label = QLabel()
        label.setWordWrap(True)
        return label

    def _apply_status(self, label: QLabel, status: DeviceStatus, connected_text: str = "Connected") -> None:
        color = _STATUS_COLORS.get(status, "#888888")
        text = {
            DeviceStatus.DISCONNECTED: "● Disconnected",
            DeviceStatus.CONNECTING: "● Connecting...",
            DeviceStatus.CONNECTED: f"● {connected_text}",
            DeviceStatus.RECEIVING_DATA: f"● {connected_text} — receiving data",
            DeviceStatus.WARNING: "● Connection warning — run Check Connection",
            DeviceStatus.ERROR: "● Connection error",
        }.get(status, f"● {status.value}")
        label.setText(text)
        label.setStyleSheet(
            "QLabel { "
            f"color: {color}; background: #f6f6f6; border: 1px solid {color}; "
            "border-radius: 5px; padding: 8px; font-weight: bold; }"
        )

    @staticmethod
    def _fill_combo(combo: QComboBox, devices: list[dict], optional: bool = False) -> None:
        previous = combo.currentData()
        combo.clear()
        if optional:
            combo.addItem("(None — use only one keyboard)", "")
        for device in devices:
            combo.addItem(str(device.get("display") or device.get("name") or device.get("id")), device.get("id"))
        if previous:
            for i in range(combo.count()):
                if combo.itemData(i) == previous:
                    combo.setCurrentIndex(i)
                    break


class KeyboardConnectionPanel(_SelectableInputPanel):
    """Bind one required + one optional physical keyboard."""

    def __init__(self, controller: ApplicationController, parent: QWidget | None = None) -> None:
        super().__init__("Keyboard Connection (1 required, maximum 2)", controller, DeviceType.KEYBOARD, parent)

        root = QVBoxLayout(self)
        intro = QLabel(
            "Select the physical keyboard(s) that will be used for explicit feedback. "
            "The console supports <b>one required keyboard and one optional second keyboard</b>. "
            "On Windows, keyboards are enumerated using their Raw Input hardware identities so two connected keyboards can be distinguished by device identity."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        form = QFormLayout()
        self._keyboard1 = QComboBox()
        self._keyboard1.setMinimumWidth(520)
        self._keyboard2 = QComboBox()
        self._keyboard2.setMinimumWidth(520)
        form.addRow("Keyboard 1 (required):", self._keyboard1)
        form.addRow("Keyboard 2 (optional):", self._keyboard2)
        root.addLayout(form)

        buttons = QHBoxLayout()
        self._refresh_btn = QPushButton("Refresh Devices")
        self._refresh_btn.clicked.connect(self._refresh_devices)
        self._connect_btn = QPushButton("Connect Selected Keyboard(s)")
        self._connect_btn.clicked.connect(self._connect)
        self._check_btn = QPushButton("Check Connection")
        self._check_btn.clicked.connect(self._check)
        self._disconnect_btn = QPushButton("Disconnect")
        self._disconnect_btn.clicked.connect(lambda: controller.device_manager.disconnect_device(DeviceType.KEYBOARD))
        buttons.addWidget(self._refresh_btn)
        buttons.addWidget(self._connect_btn)
        buttons.addWidget(self._check_btn)
        buttons.addWidget(self._disconnect_btn)
        buttons.addStretch()
        root.addLayout(buttons)

        self._status = self._status_banner()
        root.addWidget(self._status)
        self._detail = QLabel("No keyboard selected.")
        self._detail.setWordWrap(True)
        self._detail.setStyleSheet("color: #666;")
        root.addWidget(self._detail)

        controller.device_manager.device_status_changed.connect(self._on_status_changed)
        self._refresh_devices()
        self._refresh_status(controller.device_manager.status(DeviceType.KEYBOARD))

    def _refresh_devices(self) -> None:
        try:
            devices = self._controller.device_manager.list_keyboards()
        except Exception as exc:
            devices = []
            self._detail.setText(f"Could not enumerate keyboards: {exc}")
        self._fill_combo(self._keyboard1, devices, optional=False)
        self._fill_combo(self._keyboard2, devices, optional=True)
        if not devices:
            self._keyboard1.addItem("No physical keyboards found", "")
            self._detail.setText(
                "No keyboard devices were enumerated. On Windows, make sure the keyboard is connected and then click Refresh Devices."
            )
        else:
            self._detail.setText(f"Found {len(devices)} keyboard device(s). Select 1 or 2, then connect them to the study console.")
        self._update_controls()

    def _connect(self) -> None:
        first = str(self._keyboard1.currentData() or "")
        second = str(self._keyboard2.currentData() or "")
        ids = [x for x in (first, second) if x]
        if not first:
            QMessageBox.warning(self, "Keyboard Selection", "Select Keyboard 1 before connecting.")
            return
        if second and second == first:
            QMessageBox.warning(self, "Keyboard Selection", "Keyboard 1 and Keyboard 2 must be different devices.")
            return
        try:
            self._controller.device_manager.connect_keyboards(ids)
            self._detail.setText(
                f"Connected {len(ids)} keyboard(s). The console has stored their physical device identities for this run."
            )
        except Exception as exc:
            QMessageBox.critical(self, "Keyboard Connection Failed", str(exc))
            self._detail.setText(f"Keyboard connection failed: {exc}")
        self._update_controls()

    def _check(self) -> None:
        ok, message = self._controller.device_manager.check_keyboards()
        self._detail.setText(("✓ " if ok else "✗ ") + message)
        if not ok:
            QMessageBox.warning(self, "Keyboard Connection Check", message)

    def _on_status_changed(self, device_type: DeviceType, status: DeviceStatus) -> None:
        if device_type == DeviceType.KEYBOARD:
            self._refresh_status(status)

    def _refresh_status(self, status: DeviceStatus) -> None:
        stats = self._controller.device_manager.keyboard_stats()
        count = int(stats.get("count", 0))
        connected_text = f"{count} keyboard(s) connected" if count else "Keyboard connected"
        self._apply_status(self._status, status, connected_text)
        if count:
            self._detail.setText("Selected: " + " | ".join(stats.get("selected_names", [])))
        self._update_controls()

    def _update_controls(self) -> None:
        status = self._controller.device_manager.status(DeviceType.KEYBOARD)
        connected = status in (DeviceStatus.CONNECTED, DeviceStatus.RECEIVING_DATA, DeviceStatus.WARNING)
        busy = status == DeviceStatus.CONNECTING
        self._keyboard1.setEnabled(not connected and not busy)
        self._keyboard2.setEnabled(not connected and not busy)
        self._refresh_btn.setEnabled(not connected and not busy)
        self._connect_btn.setEnabled(not connected and not busy and bool(self._keyboard1.currentData()))
        self._check_btn.setEnabled(connected)
        self._disconnect_btn.setEnabled(status != DeviceStatus.DISCONNECTED)


class JoystickConnectionPanel(_SelectableInputPanel):
    def __init__(self, controller: ApplicationController, parent: QWidget | None = None) -> None:
        super().__init__("Joystick / Gamepad Connection (maximum 1)", controller, DeviceType.JOYSTICK, parent)
        root = QVBoxLayout(self)
        intro = QLabel(
            "Select the joystick/gamepad used for explicit feedback. The console opens exactly one selected device through SDL/pygame and polls it while connected."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        row = QHBoxLayout()
        row.addWidget(QLabel("Available joystick:"))
        self._combo = QComboBox()
        self._combo.setMinimumWidth(520)
        row.addWidget(self._combo, 1)
        self._refresh_btn = QPushButton("Refresh Devices")
        self._refresh_btn.clicked.connect(self._refresh_devices)
        row.addWidget(self._refresh_btn)
        root.addLayout(row)

        buttons = QHBoxLayout()
        self._connect_btn = QPushButton("Connect Joystick")
        self._connect_btn.clicked.connect(self._connect)
        self._check_btn = QPushButton("Check Connection")
        self._check_btn.clicked.connect(self._check)
        self._disconnect_btn = QPushButton("Disconnect")
        self._disconnect_btn.clicked.connect(lambda: controller.device_manager.disconnect_device(DeviceType.JOYSTICK))
        buttons.addWidget(self._connect_btn)
        buttons.addWidget(self._check_btn)
        buttons.addWidget(self._disconnect_btn)
        buttons.addStretch()
        root.addLayout(buttons)

        self._status = self._status_banner()
        root.addWidget(self._status)
        self._detail = QLabel("No joystick connected.")
        self._detail.setWordWrap(True)
        self._detail.setStyleSheet("color: #666;")
        root.addWidget(self._detail)

        controller.device_manager.device_status_changed.connect(self._on_status_changed)
        self._refresh_devices()
        self._refresh_status(controller.device_manager.status(DeviceType.JOYSTICK))

    def _refresh_devices(self) -> None:
        devices = self._controller.device_manager.list_joysticks()
        self._fill_combo(self._combo, devices)
        if not devices:
            self._combo.addItem("No joystick/gamepad found", "")
            self._detail.setText(
                "No joystick/gamepad was detected. Connect it to Windows, wait a moment, and click Refresh Devices. "
                "If this is the first run of v1.0, make sure pygame is installed from requirements.txt."
            )
        else:
            self._detail.setText(f"Found {len(devices)} joystick/gamepad device(s).")
        self._update_controls()

    def _connect(self) -> None:
        device_id = str(self._combo.currentData() or "")
        if not device_id:
            QMessageBox.warning(self, "Joystick Selection", "Select a joystick before connecting.")
            return
        try:
            self._controller.device_manager.connect_joystick(device_id)
        except Exception as exc:
            QMessageBox.critical(self, "Joystick Connection Failed", str(exc))
            self._detail.setText(f"Joystick connection failed: {exc}")
        self._update_controls()

    def _check(self) -> None:
        ok, message = self._controller.device_manager.check_joystick()
        stats = self._controller.device_manager.joystick_stats()
        live = ""
        if ok:
            axes = stats.get("axes", [])
            buttons = stats.get("buttons", [])
            live = f" Axes: {len(axes)}; buttons: {len(buttons)}."
        self._detail.setText(("✓ " if ok else "✗ ") + message + live)
        if not ok:
            QMessageBox.warning(self, "Joystick Connection Check", message)

    def _on_status_changed(self, device_type: DeviceType, status: DeviceStatus) -> None:
        if device_type == DeviceType.JOYSTICK:
            self._refresh_status(status)

    def _refresh_status(self, status: DeviceStatus) -> None:
        stats = self._controller.device_manager.joystick_stats()
        name = stats.get("selected_name") or "Joystick"
        self._apply_status(self._status, status, f"{name} connected")
        if stats.get("selected_name"):
            self._detail.setText(f"Selected: {stats['selected_name']}")
        elif stats.get("last_error"):
            self._detail.setText(str(stats["last_error"]))
        self._update_controls()

    def _update_controls(self) -> None:
        status = self._controller.device_manager.status(DeviceType.JOYSTICK)
        connected = status in (DeviceStatus.CONNECTED, DeviceStatus.RECEIVING_DATA, DeviceStatus.WARNING)
        busy = status == DeviceStatus.CONNECTING
        self._combo.setEnabled(not connected and not busy)
        self._refresh_btn.setEnabled(not connected and not busy)
        self._connect_btn.setEnabled(not connected and not busy and bool(self._combo.currentData()))
        self._check_btn.setEnabled(connected)
        self._disconnect_btn.setEnabled(status != DeviceStatus.DISCONNECTED)


class MicrophoneConnectionPanel(_SelectableInputPanel):
    def __init__(self, controller: ApplicationController, parent: QWidget | None = None) -> None:
        super().__init__("Microphone Connection (maximum 1)", controller, DeviceType.MICROPHONE, parent)
        root = QVBoxLayout(self)
        intro = QLabel(
            "Select the microphone used for voice feedback. Connecting opens a small input-only monitoring stream. "
            "The console marks the microphone as receiving data only after real audio callbacks reach the application."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        row = QHBoxLayout()
        row.addWidget(QLabel("Available microphone:"))
        self._combo = QComboBox()
        self._combo.setMinimumWidth(520)
        row.addWidget(self._combo, 1)
        self._refresh_btn = QPushButton("Refresh Devices")
        self._refresh_btn.clicked.connect(self._refresh_devices)
        row.addWidget(self._refresh_btn)
        root.addLayout(row)

        buttons = QHBoxLayout()
        self._connect_btn = QPushButton("Connect Microphone")
        self._connect_btn.clicked.connect(self._connect)
        self._check_btn = QPushButton("Check Live Audio")
        self._check_btn.clicked.connect(self._check)
        self._disconnect_btn = QPushButton("Disconnect")
        self._disconnect_btn.clicked.connect(lambda: controller.device_manager.disconnect_device(DeviceType.MICROPHONE))
        buttons.addWidget(self._connect_btn)
        buttons.addWidget(self._check_btn)
        buttons.addWidget(self._disconnect_btn)
        buttons.addStretch()
        root.addLayout(buttons)

        self._status = self._status_banner()
        root.addWidget(self._status)
        self._detail = QLabel("No microphone connected.")
        self._detail.setWordWrap(True)
        self._detail.setStyleSheet("color: #666;")
        root.addWidget(self._detail)

        meter_row = QHBoxLayout()
        meter_row.addWidget(QLabel("Current input level:"))
        self._level = QProgressBar()
        self._level.setRange(0, 100)
        self._level.setValue(0)
        self._level.setFormat("%p%")
        meter_row.addWidget(self._level, 1)
        root.addLayout(meter_row)

        controller.device_manager.device_status_changed.connect(self._on_status_changed)
        self._stats_timer = QTimer(self)
        self._stats_timer.setInterval(250)
        self._stats_timer.timeout.connect(self._update_live_stats)
        self._stats_timer.start()

        self._refresh_devices()
        self._refresh_status(controller.device_manager.status(DeviceType.MICROPHONE))

    def _refresh_devices(self) -> None:
        devices = self._controller.device_manager.list_microphones()
        self._fill_combo(self._combo, devices)
        if not devices:
            self._combo.addItem("No microphone input found", "")
            self._detail.setText(
                "No microphone input was detected. Check Windows microphone permissions/device settings and click Refresh Devices. "
                "Make sure sounddevice is installed from requirements.txt."
            )
        else:
            self._detail.setText(f"Found {len(devices)} microphone input device(s).")
        self._update_controls()

    def _connect(self) -> None:
        device_id = str(self._combo.currentData() or "")
        if not device_id:
            QMessageBox.warning(self, "Microphone Selection", "Select a microphone before connecting.")
            return
        try:
            self._controller.device_manager.connect_microphone(device_id)
        except Exception as exc:
            QMessageBox.critical(self, "Microphone Connection Failed", str(exc))
            self._detail.setText(f"Microphone connection failed: {exc}")
        self._update_controls()

    def _check(self) -> None:
        ok, message = self._controller.device_manager.check_microphone()
        self._detail.setText(("✓ " if ok else "✗ ") + message)
        if not ok:
            QMessageBox.warning(self, "Microphone Connection Check", message)

    def _update_live_stats(self) -> None:
        stats = self._controller.device_manager.microphone_stats()
        peak = max(0.0, min(1.0, float(stats.get("peak_level", 0.0) or 0.0)))
        # A square-root display curve makes normal speech visible without changing recorded data.
        self._level.setValue(int((peak ** 0.5) * 100))
        status = self._controller.device_manager.status(DeviceType.MICROPHONE)
        if status in (DeviceStatus.CONNECTED, DeviceStatus.RECEIVING_DATA, DeviceStatus.WARNING):
            age = stats.get("last_data_age_s")
            if age is not None:
                self._detail.setText(
                    f"Selected: {stats.get('selected_name') or 'Microphone'} | "
                    f"audio callbacks: {int(stats.get('callback_count', 0)):,} | "
                    f"last audio data: {float(age):.2f} s ago"
                )

    def _on_status_changed(self, device_type: DeviceType, status: DeviceStatus) -> None:
        if device_type == DeviceType.MICROPHONE:
            self._refresh_status(status)

    def _refresh_status(self, status: DeviceStatus) -> None:
        stats = self._controller.device_manager.microphone_stats()
        name = stats.get("selected_name") or "Microphone"
        self._apply_status(self._status, status, f"{name} connected")
        if status == DeviceStatus.DISCONNECTED:
            self._level.setValue(0)
        if stats.get("last_error") and status in (DeviceStatus.WARNING, DeviceStatus.ERROR):
            self._detail.setText(str(stats["last_error"]))
        self._update_controls()

    def _update_controls(self) -> None:
        status = self._controller.device_manager.status(DeviceType.MICROPHONE)
        connected = status in (DeviceStatus.CONNECTED, DeviceStatus.RECEIVING_DATA, DeviceStatus.WARNING)
        busy = status == DeviceStatus.CONNECTING
        self._combo.setEnabled(not connected and not busy)
        self._refresh_btn.setEnabled(not connected and not busy)
        self._connect_btn.setEnabled(not connected and not busy and bool(self._combo.currentData()))
        self._check_btn.setEnabled(connected)
        self._disconnect_btn.setEnabled(status != DeviceStatus.DISCONNECTED)


class DeviceRow(QWidget):
    """Simple row retained only for hardware that is still a placeholder."""

    def __init__(
        self,
        controller: ApplicationController,
        device_type: DeviceType,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._device_type = device_type

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        self._dot = QLabel("\u25CF")
        self._dot.setFixedWidth(18)
        layout.addWidget(self._dot)

        name_col = QVBoxLayout()
        name_col.addWidget(QLabel(f"<b>{device_type.value}</b>"))
        hint_label = QLabel(_DEVICE_HINTS.get(device_type, ""))
        hint_label.setStyleSheet("color: #777; font-size: 11px;")
        name_col.addWidget(hint_label)
        layout.addLayout(name_col, 1)

        self._status_label = QLabel(DeviceStatus.DISCONNECTED.value)
        self._status_label.setMinimumWidth(110)
        layout.addWidget(self._status_label)
        self._connect_btn = QPushButton("Connect Placeholder")
        self._connect_btn.clicked.connect(lambda: controller.device_manager.connect_device(device_type))
        layout.addWidget(self._connect_btn)
        self._disconnect_btn = QPushButton("Disconnect")
        self._disconnect_btn.clicked.connect(lambda: controller.device_manager.disconnect_device(device_type))
        layout.addWidget(self._disconnect_btn)
        self._refresh(controller.device_manager.status(device_type))

    def on_status_changed(self, device_type: DeviceType, status: DeviceStatus) -> None:
        if device_type == self._device_type:
            self._refresh(status)

    def _refresh(self, status: DeviceStatus) -> None:
        self._status_label.setText(status.value)
        color = _STATUS_COLORS.get(status, "#888888")
        self._dot.setStyleSheet(f"color: {color}; font-size: 16px;")
        connected = status in (DeviceStatus.CONNECTED, DeviceStatus.RECEIVING_DATA)
        busy = status == DeviceStatus.CONNECTING
        self._connect_btn.setEnabled(not connected and not busy)
        self._disconnect_btn.setEnabled(status != DeviceStatus.DISCONNECTED)


class DevicesPage(QWidget):
    def __init__(self, controller: ApplicationController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._controller = controller

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        title = QLabel("Devices")
        title.setStyleSheet("font-size: 20px; font-weight: bold;")
        outer.addWidget(title)

        subtitle = QLabel(
            "Connect and verify the exact hardware used for participant data collection. "
            "HoloLens 2, Shimmer, keyboards, joystick/gamepad, and microphone now have real device integrations. "
            "Use each guided panel to connect and verify live data before participant collection."
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color: #666;")
        outer.addWidget(subtitle)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        root = QVBoxLayout(content)
        root.setContentsMargins(2, 2, 8, 8)
        root.setSpacing(12)

        self._hololens_panel = HoloLensConnectionPanel(controller)
        root.addWidget(self._hololens_panel)

        self._shimmer_panel = ShimmerConnectionPanel(controller)
        root.addWidget(self._shimmer_panel)

        self._keyboard_panel = KeyboardConnectionPanel(controller)
        root.addWidget(self._keyboard_panel)

        self._joystick_panel = JoystickConnectionPanel(controller)
        root.addWidget(self._joystick_panel)

        self._microphone_panel = MicrophoneConnectionPanel(controller)
        root.addWidget(self._microphone_panel)

        bottom = QHBoxLayout()
        disconnect_all_btn = QPushButton("Disconnect All Devices")
        disconnect_all_btn.clicked.connect(controller.device_manager.disconnect_all)
        bottom.addWidget(disconnect_all_btn)
        bottom.addStretch()
        root.addLayout(bottom)

        root.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

