"""Real Shimmer3 GSR+ Bluetooth integration for the HINT Study Console.

The class owns the serial port and a background reader thread so Shimmer's
continuous Bluetooth stream never blocks the Qt GUI thread.  It configures
GSR + optical PPG (internal ADC A13), starts a 128 Hz stream, writes every
sample to CSV, and exposes lightweight live statistics to the Devices page.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import threading
import time
from typing import Any

from PySide6.QtCore import Signal

from devices.base_device import BaseDevice
from devices import shimmer_protocol as proto
from models.enums import DeviceStatus, DeviceType
from models.trial import Trial

try:  # Keep the rest of the GUI launchable even before requirements are installed.
    import serial
    from serial import SerialException
    from serial.tools import list_ports
except ImportError:  # pragma: no cover - covered indirectly by graceful error paths
    serial = None
    SerialException = Exception
    list_ports = None

logger = logging.getLogger(__name__)


class ShimmerConnectionError(RuntimeError):
    pass


@dataclass
class _TrialRecording:
    """Open trial-scoped physiological recording owned by the stream thread."""

    trial_id: str
    participant_code: str
    session_id: str
    condition_code: str
    run_code: str
    condition_name: str
    study: str
    environment: str
    feedback_timing: str
    modality: str
    practice: bool
    trial_started_at: float
    recording_started_at: float
    csv_path: Path
    metadata_path: Path
    file_handle: Any
    writer: Any
    sample_count: int = 0
    last_flush_monotonic: float = 0.0


class ShimmerDevice(BaseDevice):
    """Shimmer3 GSR+ device configured for the HINT physiological stream."""

    connection_progress = Signal(int, str)  # percent, human-readable step
    log_message = Signal(str)
    stream_stats_changed = Signal(object)   # dict snapshot

    BAUD_RATE = 1_000_000
    SAMPLE_RATE_HZ = 128.0
    SERIAL_TIMEOUT_S = 0.10
    COMMAND_TIMEOUT_S = 2.0
    FIRST_SAMPLE_TIMEOUT_S = 4.0
    STALE_STREAM_S = 2.0

    def __init__(self, data_dir: Path, parent=None) -> None:
        super().__init__(DeviceType.SHIMMER, parent)
        self._data_dir = Path(data_dir)
        self._port_name: str | None = None
        self._serial = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

        self._hardware_version: int | None = None
        self._firmware_id: int | None = None
        self._firmware_major: int | None = None
        self._firmware_minor: int | None = None
        self._firmware_internal: int | None = None
        self._timestamp_bytes = 3
        self._inquiry: proto.InquiryInfo | None = None

        self._sample_count = 0
        self._latest_packet_monotonic: float | None = None
        self._stream_started_monotonic: float | None = None
        self._latest_values: dict[str, Any] = {}
        self._log_path: Path | None = None
        self._last_error = ""

        self._ts_wraps = 0
        self._last_timestamp_raw: int | None = None

        # The connection-level stream stays active after the device is connected.
        # Experimental Study 1/2 trials selectively tee those same samples into
        # trial-local sensors/shimmer_gsr_ppg.csv files.
        self._recording_lock = threading.Lock()
        self._trial_recording: _TrialRecording | None = None

    # ------------------------------------------------------------------
    # Public API used by DeviceManager / GUI

    @staticmethod
    def serial_available() -> bool:
        return serial is not None and list_ports is not None

    @classmethod
    def available_ports(cls) -> list[dict[str, str | bool]]:
        if not cls.serial_available():
            return []
        rows: list[dict[str, str | bool]] = []
        for item in list_ports.comports():
            desc = item.description or ""
            hwid = item.hwid or ""
            likely_bt = "bluetooth" in desc.lower() or "bth" in hwid.lower()
            rows.append({
                "device": item.device,
                "description": desc,
                "hwid": hwid,
                "likely_bluetooth": likely_bt,
            })
        rows.sort(key=lambda row: (not bool(row["likely_bluetooth"]), str(row["device"])))
        return rows

    def set_port(self, port_name: str) -> None:
        self._port_name = port_name.strip() if port_name else None

    @property
    def port_name(self) -> str | None:
        return self._port_name

    def connect_device(self) -> None:
        if self.status in (DeviceStatus.CONNECTING, DeviceStatus.CONNECTED, DeviceStatus.RECEIVING_DATA):
            return
        if not self._port_name:
            self._last_error = "Select a Shimmer Bluetooth COM port first."
            self.log_message.emit(self._last_error)
            self._set_status(DeviceStatus.ERROR)
            return
        if not self.serial_available():
            self._last_error = "pyserial is not installed. Run: pip install -r requirements.txt"
            self.log_message.emit(self._last_error)
            self._set_status(DeviceStatus.ERROR)
            return

        self._stop_event.clear()
        self._reset_runtime_stats()
        self._set_status(DeviceStatus.CONNECTING)
        self.connection_progress.emit(3, "Starting Shimmer connection...")
        self._thread = threading.Thread(
            target=self._connection_worker,
            name="ShimmerStreamWorker",
            daemon=True,
        )
        self._thread.start()

    def disconnect_device(self) -> None:
        self.stop_trial_recording(reason="device_disconnected")
        self._stop_event.set()
        ser = self._serial
        if ser is not None:
            try:
                if getattr(ser, "is_open", False):
                    # Best effort only; streaming thread may already be exiting.
                    ser.write(bytes((proto.STOP_STREAMING_COMMAND,)))
                    ser.flush()
            except Exception:
                logger.debug("Could not send Shimmer stop-stream command", exc_info=True)
            try:
                ser.close()
            except Exception:
                logger.debug("Could not close Shimmer serial port", exc_info=True)
        self._serial = None
        self.connection_progress.emit(0, "Disconnected")
        self.log_message.emit("Shimmer disconnected.")
        self._set_status(DeviceStatus.DISCONNECTED)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            latest_age = None
            if self._latest_packet_monotonic is not None:
                latest_age = max(0.0, time.monotonic() - self._latest_packet_monotonic)
            elapsed = None
            packet_rate = 0.0
            if self._stream_started_monotonic is not None:
                elapsed = max(0.001, time.monotonic() - self._stream_started_monotonic)
                packet_rate = self._sample_count / elapsed
            base = {
                "port": self._port_name or "",
                "hardware_version": self._hardware_version,
                "firmware": self._firmware_string(),
                "sampling_rate_hz": (
                    self._inquiry.sampling_rate_hz if self._inquiry else self.SAMPLE_RATE_HZ
                ),
                "channel_ids": list(self._inquiry.channel_ids) if self._inquiry else [],
                "sample_count": self._sample_count,
                "packet_rate_hz": packet_rate,
                "last_packet_age_s": latest_age,
                "latest": dict(self._latest_values),
                "log_path": str(self._log_path) if self._log_path else "",
                "last_error": self._last_error,
                "status": self.status.value,
            }
        with self._recording_lock:
            rec = self._trial_recording
            base.update({
                "study_recording_active": rec is not None,
                "study_recording_trial_id": rec.trial_id if rec else "",
                "study_recording_path": str(rec.csv_path) if rec else "",
                "study_recording_sample_count": rec.sample_count if rec else 0,
                "study_recording_elapsed_s": (
                    max(0.0, time.time() - rec.recording_started_at) if rec else 0.0
                ),
            })
        return base

    def is_stream_healthy(self, max_age_s: float | None = None) -> bool:
        max_age_s = self.STALE_STREAM_S if max_age_s is None else max_age_s
        snapshot = self.stats()
        age = snapshot["last_packet_age_s"]
        return (
            self.status == DeviceStatus.RECEIVING_DATA
            and snapshot["sample_count"] > 0
            and age is not None
            and age <= max_age_s
        )

    # ------------------------------------------------------------------
    # Trial-scoped physiological recording

    def start_trial_recording(self, trial: Trial) -> Path:
        """Save incoming GSR/PPG samples into the active experimental trial.

        The Shimmer remains continuously connected/streaming.  This method only
        opens a second, trial-local CSV sink, so starting/stopping a study does
        not interrupt the Bluetooth stream. Practice/training trials are kept
        separate from primary study data and are therefore rejected here.
        """

        if trial.practice:
            raise ValueError("Trial-scoped Shimmer study recording is only for experimental trials")
        if trial.trial_path is None:
            raise ValueError("Trial has no storage directory")
        if trial.started_at is None:
            raise ValueError("Trial must be started before Shimmer recording begins")
        if not self.is_stream_healthy():
            raise ShimmerConnectionError(
                "Shimmer is not currently receiving fresh GSR/PPG samples. "
                "Use Devices -> Check Live Data before starting the study trial."
            )

        sensor_dir = trial.trial_path / "sensors"
        sensor_dir.mkdir(parents=True, exist_ok=True)
        csv_path = sensor_dir / "shimmer_gsr_ppg.csv"
        metadata_path = sensor_dir / "shimmer_recording_metadata.json"
        started = time.time()

        with self._recording_lock:
            if self._trial_recording is not None:
                if self._trial_recording.trial_id == trial.trial_id:
                    return self._trial_recording.csv_path
                raise RuntimeError(
                    f"Shimmer is already recording trial {self._trial_recording.trial_id}"
                )

            handle = open(csv_path, "w", newline="", encoding="utf-8")
            writer = csv.writer(handle)
            writer.writerow([
                "participant_code",
                "session_id",
                "trial_id",
                "condition_code",
                "run_code",
                "condition_name",
                "study",
                "environment",
                "feedback_timing",
                "modality",
                "practice",
                "trial_started_at_epoch",
                "host_time_epoch",
                "host_time_iso_utc",
                "trial_elapsed_s",
                "host_monotonic",
                "stream_sample_index",
                "trial_sample_index",
                "shimmer_timestamp_raw",
                "shimmer_timestamp_seconds",
                "gsr_raw",
                "gsr_adc",
                "gsr_range",
                "ppg_raw",
            ])
            handle.flush()

            self._trial_recording = _TrialRecording(
                trial_id=trial.trial_id,
                participant_code=trial.participant_code,
                session_id=trial.session_id,
                condition_code=trial.condition_code,
                run_code=trial.run_code,
                condition_name=trial.condition_name,
                study=trial.condition.study.value,
                environment=trial.condition.environment.value,
                feedback_timing=trial.condition.feedback_timing.value,
                modality=trial.condition.modality.value,
                practice=trial.practice,
                trial_started_at=float(trial.started_at),
                recording_started_at=started,
                csv_path=csv_path,
                metadata_path=metadata_path,
                file_handle=handle,
                writer=writer,
                last_flush_monotonic=time.monotonic(),
            )
            self._write_trial_metadata_locked(ended_at=None, reason="recording_started")

        self._log(
            f"STUDY RECORDING STARTED: {trial.trial_id} -> {csv_path}"
        )
        self.stream_stats_changed.emit(self.stats())
        return csv_path

    def stop_trial_recording(self, trial_id: str | None = None, reason: str = "trial_ended") -> dict[str, Any] | None:
        """Flush and close the current trial-local physiological CSV."""

        summary: dict[str, Any] | None = None
        with self._recording_lock:
            rec = self._trial_recording
            if rec is None:
                return None
            if trial_id is not None and rec.trial_id != trial_id:
                return None

            ended = time.time()
            try:
                rec.file_handle.flush()
            except Exception:
                logger.exception("Could not flush trial-local Shimmer CSV")
            try:
                rec.file_handle.close()
            except Exception:
                logger.exception("Could not close trial-local Shimmer CSV")

            summary = {
                "trial_id": rec.trial_id,
                "path": str(rec.csv_path),
                "metadata_path": str(rec.metadata_path),
                "sample_count": rec.sample_count,
                "started_at": rec.recording_started_at,
                "ended_at": ended,
                "reason": reason,
            }
            try:
                self._write_trial_metadata_locked(ended_at=ended, reason=reason)
            except Exception:
                logger.exception("Could not finalize Shimmer recording metadata")
            finally:
                self._trial_recording = None

        self._log(
            f"STUDY RECORDING STOPPED: {summary['trial_id']} — "
            f"{summary['sample_count']} samples saved to {summary['path']}"
        )
        self.stream_stats_changed.emit(self.stats())
        return summary

    def _write_trial_sample(
        self,
        *,
        now_epoch: float,
        now_mono: float,
        stream_sample_index: int,
        timestamp_raw: int,
        timestamp_seconds: float,
        values: dict[str, Any],
        gsr_adc: int | None,
        gsr_range: int | None,
    ) -> None:
        with self._recording_lock:
            rec = self._trial_recording
            if rec is None:
                return
            rec.sample_count += 1
            trial_elapsed = max(0.0, now_epoch - rec.trial_started_at)
            rec.writer.writerow([
                rec.participant_code,
                rec.session_id,
                rec.trial_id,
                rec.condition_code,
                rec.run_code,
                rec.condition_name,
                rec.study,
                rec.environment,
                rec.feedback_timing,
                rec.modality,
                int(rec.practice),
                f"{rec.trial_started_at:.6f}",
                f"{now_epoch:.6f}",
                datetime.fromtimestamp(now_epoch, tz=timezone.utc).isoformat(),
                f"{trial_elapsed:.6f}",
                f"{now_mono:.6f}",
                stream_sample_index,
                rec.sample_count,
                timestamp_raw,
                f"{timestamp_seconds:.9f}",
                values.get("gsr_raw", ""),
                gsr_adc if gsr_adc is not None else "",
                gsr_range if gsr_range is not None else "",
                values.get("ppg_raw", ""),
            ])
            if now_mono - rec.last_flush_monotonic >= 1.0:
                rec.file_handle.flush()
                rec.last_flush_monotonic = now_mono

    def _write_trial_metadata_locked(self, ended_at: float | None, reason: str) -> None:
        rec = self._trial_recording
        if rec is None:
            return
        metadata = {
            "participant_code": rec.participant_code,
            "session_id": rec.session_id,
            "trial_id": rec.trial_id,
            "condition_code": rec.condition_code,
            "run_code": rec.run_code,
            "condition_name": rec.condition_name,
            "study": rec.study,
            "environment": rec.environment,
            "feedback_timing": rec.feedback_timing,
            "modality": rec.modality,
            "practice": rec.practice,
            "trial_started_at_epoch": rec.trial_started_at,
            "recording_started_at_epoch": rec.recording_started_at,
            "recording_ended_at_epoch": ended_at,
            "samples_saved": rec.sample_count,
            "configured_sample_rate_hz": self.SAMPLE_RATE_HZ,
            "shimmer_port": self._port_name or "",
            "csv_file": rec.csv_path.name,
            "completion_reason": reason,
        }
        with open(rec.metadata_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2)

    # ------------------------------------------------------------------
    # Connection + streaming thread

    def _connection_worker(self) -> None:
        writer = None
        log_file = None
        try:
            self._progress(8, f"Opening Bluetooth serial port {self._port_name}...")
            self._serial = serial.Serial(
                self._port_name,
                self.BAUD_RATE,
                timeout=self.SERIAL_TIMEOUT_S,
                write_timeout=0.5,
            )
            self._serial.reset_input_buffer()
            self._serial.reset_output_buffer()
            self._log(f"Opened {self._port_name} at {self.BAUD_RATE:,} baud.")

            self._progress(18, "Synchronizing with Shimmer...")
            self._best_effort_stop_streaming()
            self._serial.reset_input_buffer()
            # Shimmer's current classic client performs a benign read command to
            # flush/re-align the RFCOMM byte stream before identification.
            self._write(bytes((proto.GET_SAMPLING_RATE_COMMAND,)))
            self._drain_for(0.35)
            self._serial.reset_input_buffer()

            self._progress(30, "Identifying Shimmer hardware and firmware...")
            self._read_identity()
            self._set_status(DeviceStatus.CONNECTED)
            self._log(
                f"Identified hardware={self._hardware_version}, firmware={self._firmware_string()}."
            )

            self._progress(46, "Configuring GSR + optical PPG channels...")
            self._command_ack(proto.set_sensors_command(), "select GSR + PPG sensors")
            self._command_ack(proto.set_sampling_rate_command(self.SAMPLE_RATE_HZ), "set 128 Hz sampling")
            self._command_ack(
                bytes((proto.SET_GSR_RANGE_COMMAND, proto.GSR_RANGE_AUTO)),
                "set GSR auto-range",
            )

            self._progress(60, "Enabling 3 V expansion power for the PPG probe...")
            self._command_ack(
                bytes((proto.SET_INTERNAL_EXP_POWER_ENABLE_COMMAND, 0x01)),
                "enable internal expansion power",
            )

            self._progress(72, "Verifying sensor configuration...")
            self._inquiry = self._read_inquiry()
            channels = set(self._inquiry.channel_ids)
            required = {proto.CHANNEL_GSR, proto.CHANNEL_PPG_A13}
            if not required.issubset(channels):
                found = ", ".join(f"0x{x:02X}" for x in self._inquiry.channel_ids) or "none"
                raise ShimmerConnectionError(
                    "Shimmer did not report both required channels after configuration. "
                    f"Expected GSR(0x{proto.CHANNEL_GSR:02X}) and PPG/A13(0x{proto.CHANNEL_PPG_A13:02X}); "
                    f"reported: {found}."
                )
            # Fail loudly if a firmware automatically enables extra channels that
            # this narrowly scoped HINT parser does not know how to decode.
            proto.frame_length(self._inquiry.channel_ids, self._timestamp_bytes)
            self._log(
                "Verified channels: "
                + ", ".join(f"0x{x:02X}" for x in self._inquiry.channel_ids)
                + f" at {self._inquiry.sampling_rate_hz:.2f} Hz."
            )

            self._progress(82, "Starting realtime Shimmer stream...")
            self._write(bytes((proto.START_STREAMING_COMMAND,)))
            self._wait_for_ack(self.COMMAND_TIMEOUT_S)

            self._open_log()
            log_file = open(self._log_path, "w", newline="", encoding="utf-8")
            writer = csv.writer(log_file)
            writer.writerow([
                "host_time_epoch",
                "host_time_iso_utc",
                "host_monotonic",
                "sample_index",
                "shimmer_timestamp_raw",
                "shimmer_timestamp_seconds",
                "gsr_raw",
                "gsr_adc",
                "gsr_range",
                "ppg_raw",
            ])
            log_file.flush()
            self._log(f"Realtime CSV logging started: {self._log_path}")

            self._progress(91, "Waiting for live GSR + PPG samples...")
            self._stream_started_monotonic = time.monotonic()
            first_sample_deadline = time.monotonic() + self.FIRST_SAMPLE_TIMEOUT_S
            buffer = bytearray()
            last_stats_emit = 0.0
            last_flush = time.monotonic()

            while not self._stop_event.is_set():
                chunk = self._serial.read(256)
                if chunk:
                    buffer.extend(chunk)

                consumed_any = False
                while True:
                    frame = self._extract_next_frame(buffer)
                    if frame is None:
                        break
                    consumed_any = True
                    sample = proto.parse_data_frame(
                        frame,
                        self._inquiry.channel_ids,
                        self._timestamp_bytes,
                    )
                    now_epoch = time.time()
                    now_mono = time.monotonic()
                    ts_seconds = self._unwrap_timestamp_seconds(sample.timestamp_raw)
                    with self._lock:
                        self._sample_count += 1
                        sample_index = self._sample_count
                        self._latest_packet_monotonic = now_mono
                        self._latest_values = {
                            **sample.values,
                            "gsr_adc": sample.gsr_adc,
                            "gsr_range": sample.gsr_range,
                            "shimmer_timestamp_raw": sample.timestamp_raw,
                            "shimmer_timestamp_seconds": ts_seconds,
                        }

                    writer.writerow([
                        f"{now_epoch:.6f}",
                        datetime.fromtimestamp(now_epoch, tz=timezone.utc).isoformat(),
                        f"{now_mono:.6f}",
                        sample_index,
                        sample.timestamp_raw,
                        f"{ts_seconds:.9f}",
                        sample.values.get("gsr_raw", ""),
                        sample.gsr_adc if sample.gsr_adc is not None else "",
                        sample.gsr_range if sample.gsr_range is not None else "",
                        sample.values.get("ppg_raw", ""),
                    ])

                    self._write_trial_sample(
                        now_epoch=now_epoch,
                        now_mono=now_mono,
                        stream_sample_index=sample_index,
                        timestamp_raw=sample.timestamp_raw,
                        timestamp_seconds=ts_seconds,
                        values=sample.values,
                        gsr_adc=sample.gsr_adc,
                        gsr_range=sample.gsr_range,
                    )

                    if sample_index == 1:
                        self._set_status(DeviceStatus.RECEIVING_DATA)
                        self._progress(100, "Connected and receiving live GSR + PPG data.")
                        self._log("SUCCESS: Shimmer is connected and realtime data is reaching the GUI.")
                    elif self.status == DeviceStatus.WARNING:
                        self._set_status(DeviceStatus.RECEIVING_DATA)
                        self._log("Realtime Shimmer samples resumed after a temporary stall.")

                    if now_mono - last_stats_emit >= 0.20:
                        last_stats_emit = now_mono
                        self.stream_stats_changed.emit(self.stats())
                    if now_mono - last_flush >= 1.0:
                        log_file.flush()
                        last_flush = now_mono

                if self._sample_count == 0 and time.monotonic() > first_sample_deadline:
                    raise ShimmerConnectionError(
                        "Shimmer accepted START_STREAMING but no data packets arrived within "
                        f"{self.FIRST_SAMPLE_TIMEOUT_S:.0f} s. Check Bluetooth pairing, PPG probe, "
                        "and whether another application is using this COM port."
                    )

                # Detect a live link that has stopped delivering samples. This is
                # deliberately a WARNING rather than immediate disconnect so a
                # short RFCOMM hiccup can recover without losing the recording.
                if self._sample_count > 0 and self._latest_packet_monotonic is not None:
                    stale_for = time.monotonic() - self._latest_packet_monotonic
                    if stale_for > self.STALE_STREAM_S and self.status == DeviceStatus.RECEIVING_DATA:
                        self._set_status(DeviceStatus.WARNING)
                        self._log(
                            f"WARNING: no new Shimmer sample for {stale_for:.1f} s; "
                            "use Check Live Data to verify the stream."
                        )
                        self.stream_stats_changed.emit(self.stats())

                if not chunk and not consumed_any:
                    time.sleep(0.005)

        except Exception as exc:
            if not self._stop_event.is_set():
                self._last_error = str(exc)
                logger.exception("Shimmer connection/stream failed")
                self._log(f"ERROR: {exc}")
                self.connection_progress.emit(0, f"Connection failed: {exc}")
                self._set_status(DeviceStatus.ERROR)
        finally:
            self.stop_trial_recording(reason="shimmer_stream_ended")
            if log_file is not None:
                try:
                    log_file.flush()
                    log_file.close()
                except Exception:
                    logger.debug("Could not close Shimmer CSV", exc_info=True)
            ser = self._serial
            if ser is not None:
                try:
                    if getattr(ser, "is_open", False):
                        try:
                            ser.write(bytes((proto.STOP_STREAMING_COMMAND,)))
                            ser.flush()
                            time.sleep(0.05)
                        except Exception:
                            pass
                        ser.close()
                except Exception:
                    logger.debug("Error closing Shimmer serial connection", exc_info=True)
            self._serial = None
            if self._stop_event.is_set() and self.status != DeviceStatus.DISCONNECTED:
                self._set_status(DeviceStatus.DISCONNECTED)

    # ------------------------------------------------------------------
    # Protocol helpers

    def _read_identity(self) -> None:
        self._write(bytes((proto.GET_DEVICE_VERSION_COMMAND,)))
        self._wait_for_ack(self.COMMAND_TIMEOUT_S)
        self._wait_for_opcode(proto.DEVICE_VERSION_RESPONSE, self.COMMAND_TIMEOUT_S)
        hw = self._read_exact(1, self.COMMAND_TIMEOUT_S)
        self._hardware_version = hw[0]
        # This adapter implements the classic Shimmer3 RFCOMM/virtual-COM
        # transport and Shimmer3 sensor bitmap.  Shimmer3R uses a different
        # transport/mapping, so fail explicitly rather than silently applying
        # the wrong configuration.
        if self._hardware_version != 3:
            detected = (
                "Shimmer3R" if self._hardware_version == 10
                else f"hardware version {self._hardware_version}"
            )
            raise ShimmerConnectionError(
                f"Detected {detected}. This HINT adapter currently supports the classic "
                "Shimmer3 GSR+ Bluetooth virtual-COM interface (hardware version 3)."
            )

        self._write(bytes((proto.GET_FW_VERSION_COMMAND,)))
        self._wait_for_ack(self.COMMAND_TIMEOUT_S)
        self._wait_for_opcode(proto.FW_VERSION_RESPONSE, self.COMMAND_TIMEOUT_S)
        payload = self._read_exact(6, self.COMMAND_TIMEOUT_S)
        (
            self._firmware_id,
            self._firmware_major,
            self._firmware_minor,
            self._firmware_internal,
        ) = proto.parse_firmware_payload(payload)
        self._timestamp_bytes = proto.timestamp_bytes_for_firmware(
            self._firmware_id,
            self._firmware_major,
            self._firmware_minor,
        )

    def _read_inquiry(self) -> proto.InquiryInfo:
        self._write(bytes((proto.INQUIRY_COMMAND,)))
        # Most LogAndStream firmware ACKs the command first. Be tolerant of a
        # response that arrives without an ACK by finding the response opcode.
        deadline = time.monotonic() + self.COMMAND_TIMEOUT_S
        buf = bytearray()
        while time.monotonic() < deadline:
            b = self._serial.read(1)
            if not b:
                continue
            value = b[0]
            if not buf:
                if value == proto.ACK_COMMAND_PROCESSED:
                    continue
                if value != proto.INQUIRY_RESPONSE:
                    continue
            buf.append(value)
            needed = proto.inquiry_message_length(buf)
            if needed is not None and len(buf) >= needed:
                return proto.parse_inquiry_response(bytes(buf[:needed]))
        raise ShimmerConnectionError("Timed out waiting for Shimmer inquiry response")

    def _command_ack(self, command: bytes, action: str) -> None:
        self._write(command)
        try:
            self._wait_for_ack(self.COMMAND_TIMEOUT_S)
        except Exception as exc:
            raise ShimmerConnectionError(f"Could not {action}: {exc}") from exc

    def _best_effort_stop_streaming(self) -> None:
        try:
            self._write(bytes((proto.STOP_STREAMING_COMMAND,)))
            self._drain_for(0.25)
        except Exception:
            pass

    def _write(self, payload: bytes) -> None:
        if self._serial is None or not self._serial.is_open:
            raise ShimmerConnectionError("Shimmer serial port is not open")
        self._serial.write(payload)
        self._serial.flush()

    def _wait_for_ack(self, timeout_s: float) -> None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            b = self._serial.read(1)
            if b and b[0] == proto.ACK_COMMAND_PROCESSED:
                return
        raise ShimmerConnectionError("Timed out waiting for Shimmer ACK (0xFF)")

    def _wait_for_opcode(self, opcode: int, timeout_s: float) -> None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            b = self._serial.read(1)
            if b and b[0] == opcode:
                return
        raise ShimmerConnectionError(f"Timed out waiting for response 0x{opcode:02X}")

    def _read_exact(self, n: int, timeout_s: float) -> bytes:
        deadline = time.monotonic() + timeout_s
        out = bytearray()
        while len(out) < n and time.monotonic() < deadline:
            chunk = self._serial.read(n - len(out))
            if chunk:
                out.extend(chunk)
        if len(out) != n:
            raise ShimmerConnectionError(f"Expected {n} response bytes, received {len(out)}")
        return bytes(out)

    def _drain_for(self, seconds: float) -> bytes:
        deadline = time.monotonic() + seconds
        out = bytearray()
        while time.monotonic() < deadline:
            chunk = self._serial.read(128)
            if chunk:
                out.extend(chunk)
            else:
                time.sleep(0.005)
        return bytes(out)

    def _extract_next_frame(self, buffer: bytearray) -> bytes | None:
        if self._inquiry is None:
            return None
        expected = proto.frame_length(self._inquiry.channel_ids, self._timestamp_bytes)
        # RFCOMM is an unframed byte stream. Search for the DATA_PACKET byte and
        # discard command/ACK residue until a complete frame is available.
        while buffer and buffer[0] != proto.DATA_PACKET:
            del buffer[0]
        if len(buffer) < expected:
            return None
        frame = bytes(buffer[:expected])
        del buffer[:expected]
        return frame

    # ------------------------------------------------------------------
    # Logging / stats helpers

    def _open_log(self) -> None:
        target_dir = self._data_dir / "device_logs" / "shimmer"
        target_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        port_slug = (self._port_name or "port").replace("/", "_").replace("\\", "_").replace(":", "")
        self._log_path = target_dir / f"shimmer_stream_{stamp}_{port_slug}.csv"

    def _reset_runtime_stats(self) -> None:
        with self._lock:
            self._sample_count = 0
            self._latest_packet_monotonic = None
            self._stream_started_monotonic = None
            self._latest_values = {}
        self._log_path = None
        self._last_error = ""
        self._ts_wraps = 0
        self._last_timestamp_raw = None
        self._inquiry = None

    def _unwrap_timestamp_seconds(self, raw: int) -> float:
        modulus = 1 << (8 * self._timestamp_bytes)
        if self._last_timestamp_raw is not None:
            if raw < self._last_timestamp_raw and (self._last_timestamp_raw - raw) > modulus // 2:
                self._ts_wraps += 1
        self._last_timestamp_raw = raw
        ticks = raw + self._ts_wraps * modulus
        return ticks / proto.SHIMMER_CLOCK_HZ

    def _firmware_string(self) -> str:
        if self._firmware_major is None:
            return "Unknown"
        return (
            f"ID {self._firmware_id}: "
            f"v{self._firmware_major}.{self._firmware_minor}.{self._firmware_internal}"
        )

    def _progress(self, percent: int, message: str) -> None:
        self.connection_progress.emit(percent, message)
        self._log(message)

    def _log(self, message: str) -> None:
        logger.info("Shimmer: %s", message)
        self.log_message.emit(message)
