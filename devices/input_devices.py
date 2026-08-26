"""Real selectable input-device adapters for the HINT Study Console.

This module provides the first real integrations for the non-Shimmer input
hardware used in the study:

* KeyboardDevice -- enumerate physical keyboards (Windows Raw Input; Linux
  /dev/input fallback) and bind one or two device identities to the study.
* JoystickDevice -- enumerate/open one SDL joystick through pygame.
* MicrophoneDevice -- enumerate/open one input stream through sounddevice and
  confirm that audio callbacks are arriving.

The keyboard adapter deliberately treats "connect" as binding one or two
physical keyboard identities to the current console. Normal keyboard behavior
in Windows is not disabled. Device-specific key routing can use the selected
Raw Input paths in a later input-recording layer.
"""

from __future__ import annotations

import ctypes
import glob
import logging
import os
import platform
import queue
import re
import threading
import time
from ctypes import wintypes
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PySide6.QtCore import QTimer, Signal

from devices.base_device import BaseDevice
from models.enums import DeviceStatus, DeviceType

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class InputDeviceDescriptor:
    """Stable-ish identifier + readable label shown in GUI drop-downs."""

    device_id: str
    name: str
    detail: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "id": self.device_id,
            "name": self.name,
            "detail": self.detail,
            "display": self.name if not self.detail else f"{self.name} — {self.detail}",
        }


# ---------------------------------------------------------------------------
# Keyboard enumeration


def _short_hardware_detail(raw_name: str) -> str:
    """Create a readable detail string from a Windows Raw Input path."""

    upper = raw_name.upper()
    vid = re.search(r"VID_([0-9A-F]{4})", upper)
    pid = re.search(r"PID_([0-9A-F]{4})", upper)
    parts: list[str] = []
    if vid:
        parts.append(f"VID {vid.group(1)}")
    if pid:
        parts.append(f"PID {pid.group(1)}")
    if "RDP_KBD" in upper or "ROOT#RDP_KBD" in upper:
        parts.append("Remote Desktop")
    if not parts:
        compact = raw_name.replace("\\\\?\\", "").replace("#", " ")
        parts.append(compact[:72])
    return " / ".join(parts)


def _enumerate_windows_keyboards() -> list[InputDeviceDescriptor]:
    """Enumerate Windows keyboard-class Raw Input devices via user32.

    The returned device_id is the Raw Input device path. This is exactly the
    identity later needed to distinguish two physical keyboards in WM_INPUT.
    """

    if os.name != "nt":
        return []

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    RIM_TYPEKEYBOARD = 1
    RIDI_DEVICENAME = 0x20000007

    class RAWINPUTDEVICELIST(ctypes.Structure):
        _fields_ = [
            ("hDevice", wintypes.HANDLE),
            ("dwType", wintypes.DWORD),
        ]

    get_list = user32.GetRawInputDeviceList
    get_list.argtypes = [
        ctypes.POINTER(RAWINPUTDEVICELIST),
        ctypes.POINTER(wintypes.UINT),
        wintypes.UINT,
    ]
    get_list.restype = wintypes.UINT

    get_info = user32.GetRawInputDeviceInfoW
    get_info.argtypes = [
        wintypes.HANDLE,
        wintypes.UINT,
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.UINT),
    ]
    get_info.restype = wintypes.UINT

    count = wintypes.UINT(0)
    result = get_list(None, ctypes.byref(count), ctypes.sizeof(RAWINPUTDEVICELIST))
    if result == 0xFFFFFFFF:
        raise ctypes.WinError(ctypes.get_last_error())
    if count.value == 0:
        return []

    entries = (RAWINPUTDEVICELIST * count.value)()
    result = get_list(entries, ctypes.byref(count), ctypes.sizeof(RAWINPUTDEVICELIST))
    if result == 0xFFFFFFFF:
        raise ctypes.WinError(ctypes.get_last_error())

    descriptors: list[InputDeviceDescriptor] = []
    seen: set[str] = set()
    keyboard_number = 0
    for entry in entries[: count.value]:
        if int(entry.dwType) != RIM_TYPEKEYBOARD:
            continue

        size = wintypes.UINT(0)
        get_info(entry.hDevice, RIDI_DEVICENAME, None, ctypes.byref(size))
        if size.value <= 1:
            continue
        buf = ctypes.create_unicode_buffer(size.value + 1)
        copied = get_info(entry.hDevice, RIDI_DEVICENAME, buf, ctypes.byref(size))
        if copied == 0xFFFFFFFF:
            continue
        raw_name = buf.value.strip()
        if not raw_name or raw_name in seen:
            continue
        seen.add(raw_name)
        keyboard_number += 1
        descriptors.append(
            InputDeviceDescriptor(
                device_id=raw_name,
                name=f"Keyboard {keyboard_number}",
                detail=_short_hardware_detail(raw_name),
            )
        )
    return descriptors


def _enumerate_linux_keyboards() -> list[InputDeviceDescriptor]:
    if platform.system().lower() != "linux":
        return []
    paths = sorted(glob.glob("/dev/input/by-id/*-event-kbd"))
    out: list[InputDeviceDescriptor] = []
    for index, path in enumerate(paths, start=1):
        p = Path(path)
        out.append(
            InputDeviceDescriptor(
                device_id=str(p.resolve()),
                name=f"Keyboard {index}",
                detail=p.name.replace("-event-kbd", ""),
            )
        )
    return out


def enumerate_keyboards() -> list[dict[str, str]]:
    try:
        if os.name == "nt":
            return [d.as_dict() for d in _enumerate_windows_keyboards()]
        if platform.system().lower() == "linux":
            return [d.as_dict() for d in _enumerate_linux_keyboards()]
    except Exception as exc:  # pragma: no cover - depends on host OS
        logger.exception("Could not enumerate keyboards: %s", exc)
    return []


class KeyboardDevice(BaseDevice):
    """Bind one required and one optional physical keyboard to the console."""

    MAX_KEYBOARDS = 2

    def __init__(self, parent=None) -> None:
        super().__init__(DeviceType.KEYBOARD, parent)
        self._selected_ids: list[str] = []
        self._selected_names: list[str] = []
        self._last_error = ""

    @staticmethod
    def available_devices() -> list[dict[str, str]]:
        return enumerate_keyboards()

    def connect_selected(self, device_ids: list[str]) -> None:
        ids = [str(x) for x in device_ids if str(x)]
        if not (1 <= len(ids) <= self.MAX_KEYBOARDS):
            raise ValueError("Select at least 1 and at most 2 keyboards.")
        if len(set(ids)) != len(ids):
            raise ValueError("Keyboard 1 and Keyboard 2 must be different physical devices.")

        self._set_status(DeviceStatus.CONNECTING)
        available = {d["id"]: d for d in self.available_devices()}
        missing = [device_id for device_id in ids if device_id not in available]
        if missing:
            self._last_error = "One or more selected keyboards are no longer available. Refresh the list."
            self._selected_ids = []
            self._selected_names = []
            self._set_status(DeviceStatus.ERROR)
            raise RuntimeError(self._last_error)

        self._selected_ids = ids
        self._selected_names = [available[x]["display"] for x in ids]
        self._last_error = ""
        self._set_status(DeviceStatus.CONNECTED)

    def connect_device(self) -> None:
        if not self._selected_ids:
            raise RuntimeError("Choose keyboard device(s) before connecting.")
        self.connect_selected(self._selected_ids)

    def disconnect_device(self) -> None:
        self._selected_ids = []
        self._selected_names = []
        self._last_error = ""
        self._set_status(DeviceStatus.DISCONNECTED)

    def check_connection(self) -> tuple[bool, str]:
        if not self._selected_ids:
            return False, "No keyboard is currently bound to the console."
        available_ids = {d["id"] for d in self.available_devices()}
        missing = [x for x in self._selected_ids if x not in available_ids]
        if missing:
            self._last_error = "A selected keyboard is no longer present."
            self._set_status(DeviceStatus.WARNING)
            return False, self._last_error
        if self.status == DeviceStatus.WARNING:
            self._set_status(DeviceStatus.CONNECTED)
        return True, f"{len(self._selected_ids)} selected keyboard(s) are still present."

    def stats(self) -> dict[str, Any]:
        return {
            "selected_ids": list(self._selected_ids),
            "selected_names": list(self._selected_names),
            "count": len(self._selected_ids),
            "last_error": self._last_error,
            "platform": platform.system(),
        }


# ---------------------------------------------------------------------------
# Joystick / gamepad


class JoystickDevice(BaseDevice):
    """One selected joystick/gamepad opened through pygame/SDL."""

    def __init__(self, parent=None) -> None:
        super().__init__(DeviceType.JOYSTICK, parent)
        self._pygame = None
        self._joystick = None
        self._selected_id = ""
        self._selected_name = ""
        self._last_error = ""
        self._last_poll = 0.0
        self._axes: list[float] = []
        self._buttons: list[int] = []

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(250)
        self._poll_timer.timeout.connect(self._poll)

    @staticmethod
    def _load_pygame():
        import pygame  # type: ignore

        return pygame

    @classmethod
    def available_devices(cls) -> list[dict[str, str]]:
        try:
            pygame = cls._load_pygame()
            pygame.joystick.init()
            result: list[dict[str, str]] = []
            for index in range(pygame.joystick.get_count()):
                joy = pygame.joystick.Joystick(index)
                name = joy.get_name() or f"Joystick {index + 1}"
                guid = joy.get_guid() if hasattr(joy, "get_guid") else ""
                device_id = f"{index}|{guid}|{name}"
                detail = f"Index {index}"
                if guid:
                    detail += f" / GUID {guid}"
                result.append(
                    {
                        "id": device_id,
                        "name": name,
                        "detail": detail,
                        "display": f"{name} — {detail}",
                    }
                )
            return result
        except Exception as exc:
            logger.warning("Joystick enumeration unavailable: %s", exc)
            return []

    def connect_selected(self, device_id: str) -> None:
        if not device_id:
            raise ValueError("Select a joystick before connecting.")
        self._set_status(DeviceStatus.CONNECTING)
        try:
            pygame = self._load_pygame()
            pygame.joystick.init()
            # pygame.event.pump() is used by the poller to keep joystick
            # state fresh. Initializing the display/event subsystem does not
            # create a second visible window, but makes the event queue valid.
            try:
                if hasattr(pygame, "display") and not pygame.display.get_init():
                    pygame.display.init()
            except Exception:
                logger.debug("pygame display/event subsystem could not be initialized", exc_info=True)
            parts = device_id.split("|", 2)
            index = int(parts[0])
            if index < 0 or index >= pygame.joystick.get_count():
                raise RuntimeError("Selected joystick is no longer available. Refresh the list.")
            joy = pygame.joystick.Joystick(index)
            joy.init()
            self._pygame = pygame
            self._joystick = joy
            self._selected_id = device_id
            self._selected_name = joy.get_name() or f"Joystick {index + 1}"
            self._last_error = ""
            self._poll_timer.start()
            self._poll()
            if self.status != DeviceStatus.ERROR:
                self._set_status(DeviceStatus.CONNECTED)
        except Exception as exc:
            self._last_error = str(exc)
            self._joystick = None
            self._set_status(DeviceStatus.ERROR)
            raise

    def connect_device(self) -> None:
        if not self._selected_id:
            raise RuntimeError("Choose a joystick before connecting.")
        self.connect_selected(self._selected_id)

    def disconnect_device(self) -> None:
        self._poll_timer.stop()
        try:
            if self._joystick is not None:
                self._joystick.quit()
        except Exception:
            logger.exception("Error while closing joystick")
        self._joystick = None
        self._pygame = None
        self._selected_id = ""
        self._selected_name = ""
        self._axes = []
        self._buttons = []
        self._last_error = ""
        self._set_status(DeviceStatus.DISCONNECTED)

    def _poll(self) -> None:
        if self._joystick is None:
            return
        try:
            if self._pygame is not None:
                self._pygame.event.pump()
            if hasattr(self._joystick, "get_init") and not self._joystick.get_init():
                raise RuntimeError("Joystick is no longer initialized.")
            self._axes = [
                float(self._joystick.get_axis(i))
                for i in range(int(self._joystick.get_numaxes()))
            ]
            self._buttons = [
                int(self._joystick.get_button(i))
                for i in range(int(self._joystick.get_numbuttons()))
            ]
            self._last_poll = time.monotonic()
            if self.status == DeviceStatus.WARNING:
                self._set_status(DeviceStatus.CONNECTED)
        except Exception as exc:
            self._last_error = str(exc)
            if self.status not in (DeviceStatus.DISCONNECTED, DeviceStatus.ERROR):
                self._set_status(DeviceStatus.WARNING)

    def check_connection(self) -> tuple[bool, str]:
        if self._joystick is None:
            return False, "No joystick is currently connected to the console."
        self._poll()
        healthy = self.status in (DeviceStatus.CONNECTED, DeviceStatus.RECEIVING_DATA)
        if healthy:
            return True, f"Joystick '{self._selected_name}' is open and responding to SDL polling."
        return False, self._last_error or "Joystick connection check failed."

    def stats(self) -> dict[str, Any]:
        return {
            "selected_id": self._selected_id,
            "selected_name": self._selected_name,
            "axes": list(self._axes),
            "buttons": list(self._buttons),
            "last_poll_age_s": None if not self._last_poll else time.monotonic() - self._last_poll,
            "last_error": self._last_error,
            "pygame_available": self._pygame is not None or _module_available("pygame"),
        }


# ---------------------------------------------------------------------------
# Microphone


def _module_available(name: str) -> bool:
    try:
        import importlib.util

        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


class MicrophoneDevice(BaseDevice):
    """One input-only PortAudio stream through python-sounddevice."""

    first_audio_frame = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(DeviceType.MICROPHONE, parent)
        self._sd = None
        self._stream = None
        self._selected_id = ""
        self._selected_name = ""
        self._last_error = ""
        self._last_callback = 0.0
        self._frames_received = 0
        self._callback_count = 0
        self._peak_level = 0.0
        self._got_first_frame = False
        self._sample_rate = 0.0
        self._audio_queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=256)
        self._lock = threading.Lock()

        self.first_audio_frame.connect(self._mark_receiving)
        self._health_timer = QTimer(self)
        self._health_timer.setInterval(1000)
        self._health_timer.timeout.connect(self._health_check)

    @staticmethod
    def _load_sounddevice():
        import sounddevice as sd  # type: ignore

        return sd

    @classmethod
    def available_devices(cls) -> list[dict[str, str]]:
        try:
            sd = cls._load_sounddevice()
            devices = sd.query_devices()
            result: list[dict[str, str]] = []
            for index, info in enumerate(devices):
                max_in = int(info.get("max_input_channels", 0))
                if max_in <= 0:
                    continue
                name = str(info.get("name", f"Microphone {index}"))
                hostapi = info.get("hostapi")
                rate = float(info.get("default_samplerate", 0.0) or 0.0)
                detail = f"Input {index} / {max_in} ch"
                if rate:
                    detail += f" / {rate:.0f} Hz"
                if hostapi is not None:
                    detail += f" / Host API {hostapi}"
                result.append(
                    {
                        "id": str(index),
                        "name": name,
                        "detail": detail,
                        "display": f"{name} — {detail}",
                    }
                )
            return result
        except Exception as exc:
            logger.warning("Microphone enumeration unavailable: %s", exc)
            return []

    def connect_selected(self, device_id: str) -> None:
        if not device_id:
            raise ValueError("Select a microphone before connecting.")
        self._set_status(DeviceStatus.CONNECTING)
        try:
            sd = self._load_sounddevice()
            index = int(device_id)
            info = sd.query_devices(index, "input")
            if int(info.get("max_input_channels", 0)) <= 0:
                raise RuntimeError("Selected device does not provide an audio input channel.")
            rate = float(info.get("default_samplerate", 44100.0) or 44100.0)

            self._sd = sd
            self._sample_rate = rate
            self._drain_audio_queue()
            self._selected_id = str(index)
            self._selected_name = str(info.get("name", f"Microphone {index}"))
            self._last_error = ""
            self._last_callback = 0.0
            self._frames_received = 0
            self._callback_count = 0
            self._peak_level = 0.0
            self._got_first_frame = False

            self._stream = sd.InputStream(
                device=index,
                channels=1,
                samplerate=rate,
                dtype="float32",
                callback=self._audio_callback,
            )
            self._stream.start()
            self._set_status(DeviceStatus.CONNECTED)
            self._health_timer.start()
        except Exception as exc:
            self._last_error = str(exc)
            self._stream = None
            self._set_status(DeviceStatus.ERROR)
            raise

    def connect_device(self) -> None:
        if not self._selected_id:
            raise RuntimeError("Choose a microphone before connecting.")
        self.connect_selected(self._selected_id)

    def _audio_callback(self, indata, frames, time_info, status) -> None:  # pragma: no cover - hardware callback
        try:
            peak = float(np.max(np.abs(indata))) if len(indata) else 0.0
        except Exception:
            peak = 0.0
        emit_first = False
        with self._lock:
            self._last_callback = time.monotonic()
            self._frames_received += int(frames)
            self._callback_count += 1
            self._peak_level = peak
            if not self._got_first_frame:
                self._got_first_frame = True
                emit_first = True
            if status:
                self._last_error = str(status)
        try:
            mono = np.asarray(indata[:, 0], dtype=np.float32).copy()
            try:
                self._audio_queue.put_nowait(mono)
            except queue.Full:
                try:
                    self._audio_queue.get_nowait()
                except queue.Empty:
                    pass
                try:
                    self._audio_queue.put_nowait(mono)
                except queue.Full:
                    pass
        except Exception:
            # Audio monitoring/status must never be broken by the command queue.
            pass

        if emit_first:
            self.first_audio_frame.emit()

    def _drain_audio_queue(self) -> None:
        while True:
            try:
                self._audio_queue.get_nowait()
            except queue.Empty:
                break

    def capture_phrase(
        self,
        *,
        timeout_s: float = 0.75,
        phrase_time_limit_s: float = 2.5,
        silence_s: float = 0.40,
        activation_peak: float = 0.012,
        preroll_s: float = 0.15,
    ) -> dict[str, Any] | None:
        """Capture one spoken phrase from the already-open microphone stream.

        A lightweight amplitude gate is used only to decide when a phrase begins
        and ends; speech-to-text is performed by ``VoiceCommandRecognizer``.
        This method is designed to be called from a background worker thread.
        """

        if self._stream is None or self._sample_rate <= 0:
            return None

        self._drain_audio_queue()
        start_deadline = time.monotonic() + max(0.05, float(timeout_s))
        pre_chunks: deque[np.ndarray] = deque()
        pre_samples = 0
        max_pre_samples = max(1, int(self._sample_rate * max(0.0, preroll_s)))
        phrase_chunks: list[np.ndarray] = []
        phrase_samples = 0
        max_phrase_samples = max(1, int(self._sample_rate * max(0.25, phrase_time_limit_s)))
        speech_started = False
        last_voice_at = 0.0

        while True:
            if not speech_started and time.monotonic() >= start_deadline:
                return None

            try:
                chunk = self._audio_queue.get(timeout=0.10)
            except queue.Empty:
                if speech_started and last_voice_at and time.monotonic() - last_voice_at >= silence_s:
                    break
                continue

            if chunk.size == 0:
                continue

            now = time.monotonic()
            peak = float(np.max(np.abs(chunk)))

            if not speech_started:
                pre_chunks.append(chunk)
                pre_samples += int(chunk.size)
                while pre_chunks and pre_samples > max_pre_samples:
                    removed = pre_chunks.popleft()
                    pre_samples -= int(removed.size)

                if peak >= activation_peak:
                    speech_started = True
                    phrase_chunks.extend(pre_chunks)
                    phrase_samples = sum(int(item.size) for item in phrase_chunks)
                    last_voice_at = now
                continue

            phrase_chunks.append(chunk)
            phrase_samples += int(chunk.size)
            if peak >= activation_peak:
                last_voice_at = now

            if phrase_samples >= max_phrase_samples:
                break
            if last_voice_at and now - last_voice_at >= max(0.15, silence_s):
                break

        if not phrase_chunks:
            return None

        samples = np.concatenate(phrase_chunks)
        if samples.size == 0:
            return None
        samples = np.clip(samples, -1.0, 1.0)
        pcm = (samples * 32767.0).astype(np.int16).tobytes()
        return {
            "pcm": pcm,
            "sample_rate": int(self._sample_rate),
            "duration_s": float(samples.size / self._sample_rate),
        }

    def _mark_receiving(self) -> None:
        if self.status in (DeviceStatus.CONNECTED, DeviceStatus.WARNING):
            self._set_status(DeviceStatus.RECEIVING_DATA)

    def _health_check(self) -> None:
        if self._stream is None:
            return
        try:
            active = bool(self._stream.active)
        except Exception:
            active = False
        age = None if not self._last_callback else time.monotonic() - self._last_callback
        if not active:
            self._last_error = "Microphone input stream is not active."
            self._set_status(DeviceStatus.WARNING)
        elif age is not None and age > 2.0:
            self._last_error = f"No microphone data callback for {age:.1f} seconds."
            self._set_status(DeviceStatus.WARNING)
        elif age is not None and self.status == DeviceStatus.WARNING:
            self._set_status(DeviceStatus.RECEIVING_DATA)

    def disconnect_device(self) -> None:
        self._health_timer.stop()
        try:
            if self._stream is not None:
                self._stream.stop()
                self._stream.close()
        except Exception:
            logger.exception("Error while closing microphone stream")
        self._stream = None
        self._sd = None
        self._sample_rate = 0.0
        self._drain_audio_queue()
        self._selected_id = ""
        self._selected_name = ""
        self._last_error = ""
        self._last_callback = 0.0
        self._frames_received = 0
        self._callback_count = 0
        self._peak_level = 0.0
        self._got_first_frame = False
        self._set_status(DeviceStatus.DISCONNECTED)

    def check_connection(self) -> tuple[bool, str]:
        if self._stream is None:
            return False, "No microphone is currently connected to the console."
        self._health_check()
        age = None if not self._last_callback else time.monotonic() - self._last_callback
        healthy = self.status == DeviceStatus.RECEIVING_DATA and age is not None and age < 1.5
        if healthy:
            return True, (
                f"Microphone '{self._selected_name}' is actively sending audio frames "
                f"to the GUI (last callback {age:.2f} s ago)."
            )
        if self.status == DeviceStatus.CONNECTED:
            return False, "Microphone stream is open, but no audio callback has arrived yet."
        return False, self._last_error or "Microphone connection check failed."

    def stats(self) -> dict[str, Any]:
        with self._lock:
            last_callback = self._last_callback
            frames = self._frames_received
            callbacks = self._callback_count
            peak = self._peak_level
        return {
            "selected_id": self._selected_id,
            "selected_name": self._selected_name,
            "frames_received": frames,
            "callback_count": callbacks,
            "peak_level": peak,
            "last_data_age_s": None if not last_callback else time.monotonic() - last_callback,
            "last_error": self._last_error,
            "sounddevice_available": self._sd is not None or _module_available("sounddevice"),
        }
