"""Hardware-independent tests for selectable input-device rules.

These tests monkeypatch hardware enumeration/SDKs, so they do not require a
physical keyboard, joystick, or microphone on the CI machine.
"""

from __future__ import annotations

import types

import pytest

from devices.input_devices import KeyboardDevice, JoystickDevice, MicrophoneDevice
from models.enums import DeviceStatus


def test_keyboard_requires_one_and_allows_two(monkeypatch):
    devices = [
        {"id": "kbd-a", "display": "Keyboard A"},
        {"id": "kbd-b", "display": "Keyboard B"},
        {"id": "kbd-c", "display": "Keyboard C"},
    ]
    monkeypatch.setattr(KeyboardDevice, "available_devices", staticmethod(lambda: devices))
    keyboard = KeyboardDevice()

    with pytest.raises(ValueError):
        keyboard.connect_selected([])
    with pytest.raises(ValueError):
        keyboard.connect_selected(["kbd-a", "kbd-b", "kbd-c"])
    with pytest.raises(ValueError):
        keyboard.connect_selected(["kbd-a", "kbd-a"])

    keyboard.connect_selected(["kbd-a", "kbd-b"])
    assert keyboard.status == DeviceStatus.CONNECTED
    assert keyboard.stats()["count"] == 2


def test_keyboard_check_warns_if_selected_device_disappears(monkeypatch):
    current = [
        {"id": "kbd-a", "display": "Keyboard A"},
        {"id": "kbd-b", "display": "Keyboard B"},
    ]
    monkeypatch.setattr(KeyboardDevice, "available_devices", staticmethod(lambda: list(current)))
    keyboard = KeyboardDevice()
    keyboard.connect_selected(["kbd-a", "kbd-b"])

    current.pop()
    ok, _ = keyboard.check_connection()
    assert not ok
    assert keyboard.status == DeviceStatus.WARNING


def test_joystick_connects_one_selected_device(monkeypatch):
    class FakeJoy:
        def __init__(self, index):
            self.index = index
            self._init = False

        def init(self):
            self._init = True

        def quit(self):
            self._init = False

        def get_init(self):
            return self._init

        def get_name(self):
            return "Test Gamepad"

        def get_guid(self):
            return "GUID123"

        def get_numaxes(self):
            return 2

        def get_axis(self, i):
            return 0.25 if i == 0 else -0.5

        def get_numbuttons(self):
            return 2

        def get_button(self, i):
            return int(i == 1)

    joy_obj = FakeJoy(0)
    fake_pygame = types.SimpleNamespace(
        joystick=types.SimpleNamespace(
            init=lambda: None,
            get_count=lambda: 1,
            Joystick=lambda index: joy_obj,
        ),
        event=types.SimpleNamespace(pump=lambda: None),
    )
    monkeypatch.setattr(JoystickDevice, "_load_pygame", staticmethod(lambda: fake_pygame))

    joystick = JoystickDevice()
    joystick.connect_selected("0|GUID123|Test Gamepad")
    assert joystick.status == DeviceStatus.CONNECTED
    ok, _ = joystick.check_connection()
    assert ok
    assert joystick.stats()["axes"] == [0.25, -0.5]


def test_microphone_lists_only_input_capable_devices(monkeypatch):
    fake_sd = types.SimpleNamespace(
        query_devices=lambda: [
            {"name": "Speakers", "max_input_channels": 0, "default_samplerate": 48000, "hostapi": 0},
            {"name": "USB Mic", "max_input_channels": 2, "default_samplerate": 48000, "hostapi": 0},
        ]
    )
    monkeypatch.setattr(MicrophoneDevice, "_load_sounddevice", staticmethod(lambda: fake_sd))
    devices = MicrophoneDevice.available_devices()
    assert len(devices) == 1
    assert devices[0]["name"] == "USB Mic"
