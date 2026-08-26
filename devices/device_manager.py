"""Device Manager.

Owns one device instance per :class:`~models.enums.DeviceType` and gives the
rest of the app a single place to connect/disconnect devices. Shimmer, keyboard,
joystick, microphone, and HoloLens 2 now use real hardware adapters. HoloLens
streams PV video and Extended Eye Tracking through HL2SS.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from core.event_bus import EventBus
from devices.hololens_device import HoloLensDevice
from devices.input_devices import KeyboardDevice, JoystickDevice, MicrophoneDevice
from devices.shimmer_device import ShimmerDevice
from models.enums import DeviceStatus, DeviceType, EventType
from models.event import StudyEvent
from models.trial import Trial

logger = logging.getLogger(__name__)

_EVENT_FOR_STATUS = {
    DeviceStatus.CONNECTED: EventType.DEVICE_CONNECTED,
    DeviceStatus.DISCONNECTED: EventType.DEVICE_DISCONNECTED,
    DeviceStatus.ERROR: EventType.DEVICE_ERROR,
}


class DeviceManager(QObject):
    """Connect/disconnect devices and query their status."""

    device_status_changed = Signal(object, object)  # (DeviceType, DeviceStatus)

    # Device-specific signals are forwarded so GUI code still depends on
    # DeviceManager rather than importing concrete hardware classes.
    shimmer_connection_progress = Signal(int, str)
    shimmer_log_message = Signal(str)
    shimmer_stream_stats_changed = Signal(object)

    # HoloLens-specific signals
    hololens_connection_progress = Signal(int, str)
    hololens_log_message = Signal(str)
    hololens_stream_stats_changed = Signal(object)

    def __init__(
        self,
        event_bus: EventBus,
        data_dir: Path | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._event_bus = event_bus
        data_dir = Path(data_dir) if data_dir is not None else Path("data")

        self._devices = {
            DeviceType.HOLOLENS: HoloLensDevice(parent=self),
            DeviceType.SHIMMER: ShimmerDevice(data_dir=data_dir, parent=self),
            DeviceType.JOYSTICK: JoystickDevice(parent=self),
            DeviceType.KEYBOARD: KeyboardDevice(parent=self),
            DeviceType.MICROPHONE: MicrophoneDevice(parent=self),
        }
        for device in self._devices.values():
            device.status_changed.connect(self._on_device_status_changed)

        shimmer = self.shimmer_device
        shimmer.connection_progress.connect(self.shimmer_connection_progress.emit)
        shimmer.log_message.connect(self.shimmer_log_message.emit)
        shimmer.stream_stats_changed.connect(self.shimmer_stream_stats_changed.emit)

        hololens = self.hololens_device
        hololens.connection_progress.connect(self.hololens_connection_progress.emit)
        hololens.log_message.connect(self.hololens_log_message.emit)
        hololens.stream_stats_changed.connect(self.hololens_stream_stats_changed.emit)

    @property
    def shimmer_device(self) -> ShimmerDevice:
        return self._devices[DeviceType.SHIMMER]

    def connect_device(self, device_type: DeviceType) -> None:
        self._devices[device_type].connect_device()

    def disconnect_device(self, device_type: DeviceType) -> None:
        self._devices[device_type].disconnect_device()

    def connect_all(self) -> None:
        # Every real hardware adapter now requires explicit configuration or
        # device selection in the Devices page. Intentionally do not guess.
        return

    def disconnect_all(self) -> None:
        for device_type in self._devices:
            self.disconnect_device(device_type)

    def status(self, device_type: DeviceType) -> DeviceStatus:
        return self._devices[device_type].status

    def all_statuses(self) -> dict[DeviceType, DeviceStatus]:
        return {device_type: device.status for device_type, device in self._devices.items()}

    def all_connected(self) -> bool:
        return all(
            status in (DeviceStatus.CONNECTED, DeviceStatus.RECEIVING_DATA)
            for status in self.all_statuses().values()
        )

    # ------------------------------------------------------------------
    # HoloLens GUI facade

    @property
    def hololens_device(self) -> HoloLensDevice:
        return self._devices[DeviceType.HOLOLENS]

    def connect_hololens(
        self,
        host: str,
        client_dir: str | Path,
        *,
        eye_fps: int = HoloLensDevice.DEFAULT_EYE_FPS,
        width: int = HoloLensDevice.DEFAULT_WIDTH,
        height: int = HoloLensDevice.DEFAULT_HEIGHT,
        camera_fps: int = HoloLensDevice.DEFAULT_CAMERA_FPS,
    ) -> None:
        self.hololens_device.configure(
            host,
            client_dir,
            eye_fps=eye_fps,
            width=width,
            height=height,
            camera_fps=camera_fps,
        )
        self.hololens_device.connect_device()

    def hololens_stats(self) -> dict:
        return self.hololens_device.stats()

    def hololens_latest_camera_frame(self):
        return self.hololens_device.latest_camera_frame()

    def hololens_latest_eye_data(self) -> dict:
        return self.hololens_device.latest_eye_data()

    def hololens_latest_camera_gaze_snapshot(self, distance_m: float = 1.5) -> dict:
        return self.hololens_device.latest_camera_gaze_snapshot(distance_m=distance_m)

    def check_hololens(self) -> tuple[bool, str]:
        return self.hololens_device.check_connection()

    def hololens_stream_healthy(self, max_age_s: float = 1.0) -> bool:
        return self.hololens_device.is_stream_healthy(max_age_s=max_age_s)

    def start_hololens_trial_recording(self, trial: Trial) -> dict[str, Path]:
        return self.hololens_device.start_trial_recording(trial)

    def stop_hololens_trial_recording(
        self, trial_id: str | None = None, reason: str = "trial_ended"
    ) -> dict | None:
        return self.hololens_device.stop_trial_recording(
            trial_id=trial_id, reason=reason
        )

    def hololens_client_dir_valid(self, path: str | Path | None) -> bool:
        return HoloLensDevice.client_dir_valid(path)

    # ------------------------------------------------------------------
    # Shimmer GUI facade

    def list_shimmer_ports(self) -> list[dict[str, str | bool]]:
        return ShimmerDevice.available_ports()

    def shimmer_serial_available(self) -> bool:
        return ShimmerDevice.serial_available()

    def connect_shimmer(self, port_name: str) -> None:
        self.shimmer_device.set_port(port_name)
        self.shimmer_device.connect_device()

    def shimmer_stats(self) -> dict:
        return self.shimmer_device.stats()

    def shimmer_stream_healthy(self, max_age_s: float | None = None) -> bool:
        return self.shimmer_device.is_stream_healthy(max_age_s=max_age_s)

    def start_shimmer_trial_recording(self, trial: Trial) -> Path:
        return self.shimmer_device.start_trial_recording(trial)

    def stop_shimmer_trial_recording(
        self, trial_id: str | None = None, reason: str = "trial_ended"
    ) -> dict | None:
        return self.shimmer_device.stop_trial_recording(trial_id=trial_id, reason=reason)


    # ------------------------------------------------------------------
    # Selectable input-device GUI facade

    @property
    def keyboard_device(self) -> KeyboardDevice:
        return self._devices[DeviceType.KEYBOARD]

    @property
    def joystick_device(self) -> JoystickDevice:
        return self._devices[DeviceType.JOYSTICK]

    @property
    def microphone_device(self) -> MicrophoneDevice:
        return self._devices[DeviceType.MICROPHONE]

    def list_keyboards(self) -> list[dict[str, str]]:
        return self.keyboard_device.available_devices()

    def connect_keyboards(self, device_ids: list[str]) -> None:
        self.keyboard_device.connect_selected(device_ids)

    def keyboard_stats(self) -> dict:
        return self.keyboard_device.stats()

    def check_keyboards(self) -> tuple[bool, str]:
        return self.keyboard_device.check_connection()

    def list_joysticks(self) -> list[dict[str, str]]:
        return self.joystick_device.available_devices()

    def connect_joystick(self, device_id: str) -> None:
        self.joystick_device.connect_selected(device_id)

    def joystick_stats(self) -> dict:
        return self.joystick_device.stats()

    def check_joystick(self) -> tuple[bool, str]:
        return self.joystick_device.check_connection()

    def list_microphones(self) -> list[dict[str, str]]:
        return self.microphone_device.available_devices()

    def connect_microphone(self, device_id: str) -> None:
        self.microphone_device.connect_selected(device_id)

    def microphone_stats(self) -> dict:
        return self.microphone_device.stats()

    def check_microphone(self) -> tuple[bool, str]:
        return self.microphone_device.check_connection()

    # ------------------------------------------------------------------

    def _on_device_status_changed(self, device_type: DeviceType, status: DeviceStatus) -> None:
        logger.info("Device %s -> %s", device_type.value, status.value)
        event_type = _EVENT_FOR_STATUS.get(status)
        if event_type is not None:
            self._event_bus.publish(StudyEvent(event_type=event_type, value=device_type.value))
        self.device_status_changed.emit(device_type, status)
