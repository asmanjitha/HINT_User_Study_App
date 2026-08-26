"""Headless QtCore stub for core/unit tests when PySide6 is unavailable."""

from __future__ import annotations

import importlib.util
import importlib.machinery
import sys
import types


if importlib.util.find_spec("PySide6") is None:
    class _BoundSignal:
        def __init__(self):
            self._slots = []

        def connect(self, fn):
            if fn not in self._slots:
                self._slots.append(fn)

        def disconnect(self, fn):
            if fn in self._slots:
                self._slots.remove(fn)

        def emit(self, *args, **kwargs):
            for fn in list(self._slots):
                fn(*args, **kwargs)

    class Signal:
        def __init__(self, *_args, **_kwargs):
            self._name = None

        def __set_name__(self, owner, name):
            self._name = f"__signal_{name}"

        def __get__(self, instance, owner):
            if instance is None:
                return self
            signal = getattr(instance, self._name, None)
            if signal is None:
                signal = _BoundSignal()
                setattr(instance, self._name, signal)
            return signal

    class QObject:
        def __init__(self, *_args, **_kwargs):
            pass

    class QTimer(QObject):
        def __init__(self, *_args, **_kwargs):
            super().__init__()
            self.timeout = _BoundSignal()

        def setSingleShot(self, *_args):
            pass

        def setInterval(self, *_args):
            pass

        def start(self, *_args):
            pass

        def stop(self):
            pass

    qtcore = types.ModuleType("PySide6.QtCore")
    qtcore.__spec__ = importlib.machinery.ModuleSpec("PySide6.QtCore", loader=None)
    qtcore.Signal = Signal
    qtcore.QObject = QObject
    qtcore.QTimer = QTimer

    pyside = types.ModuleType("PySide6")
    pyside.__spec__ = importlib.machinery.ModuleSpec("PySide6", loader=None, is_package=True)
    pyside.QtCore = qtcore
    sys.modules["PySide6"] = pyside
    sys.modules["PySide6.QtCore"] = qtcore
