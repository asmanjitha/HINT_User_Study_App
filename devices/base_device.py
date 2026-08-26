"""Device interface + mock implementation.

Per the README's stated plan for this milestone: a shared ``BaseDevice``
interface, with a real device class and a ``Mock*Device`` class for each
device type, so GUI/recording code only ever depends on the interface.

Shimmer and HoloLens 2 now have real hardware integrations, and keyboard,
joystick, and microphone use real selectable hardware adapters. The shared
interface lets GUI/recording code use the same status lifecycle for every
device.
"""

from __future__ import annotations

import logging
import random

from PySide6.QtCore import QObject, QTimer, Signal

from models.enums import DeviceStatus, DeviceType

logger = logging.getLogger(__name__)


class BaseDevice(QObject):
    """Common interface every device (real or mock) implements."""

    status_changed = Signal(object, object)  # (DeviceType, DeviceStatus)

    def __init__(self, device_type: DeviceType, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.device_type = device_type
        self._status = DeviceStatus.DISCONNECTED

    @property
    def status(self) -> DeviceStatus:
        return self._status

    def connect_device(self) -> None:
        raise NotImplementedError

    def disconnect_device(self) -> None:
        raise NotImplementedError

    def _set_status(self, status: DeviceStatus) -> None:
        if status == self._status:
            return
        self._status = status
        self.status_changed.emit(self.device_type, status)


class MockDevice(BaseDevice):
    """Simulated device connection, standing in for real hardware/SDKs.

    Goes Disconnected -> Connecting -> Connected after a short delay, with
    a small configurable chance of landing in Error instead (so the
    Devices page has something realistic to show/retry).
    """

    def __init__(
        self,
        device_type: DeviceType,
        connect_delay_ms: int = 700,
        fail_rate: float = 0.0,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(device_type, parent)
        self._connect_delay_ms = connect_delay_ms
        self._fail_rate = fail_rate

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._finish_connect)

    def connect_device(self) -> None:
        if self._status in (DeviceStatus.CONNECTED, DeviceStatus.RECEIVING_DATA, DeviceStatus.CONNECTING):
            return
        logger.info("Connecting %s...", self.device_type.value)
        self._set_status(DeviceStatus.CONNECTING)
        self._timer.start(self._connect_delay_ms)

    def disconnect_device(self) -> None:
        self._timer.stop()
        logger.info("Disconnecting %s", self.device_type.value)
        self._set_status(DeviceStatus.DISCONNECTED)

    def _finish_connect(self) -> None:
        if random.random() < self._fail_rate:
            logger.warning("%s failed to connect", self.device_type.value)
            self._set_status(DeviceStatus.ERROR)
        else:
            self._set_status(DeviceStatus.CONNECTED)
