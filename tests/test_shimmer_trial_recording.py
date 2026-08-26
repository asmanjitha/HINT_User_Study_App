"""Smoke test for trial-local Shimmer CSV recording without physical hardware."""

from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path
import sys
import time
import types


def _install_qt_stub_if_needed() -> None:
    if importlib.util.find_spec("PySide6") is not None:
        return

    class DummySignal:
        def __init__(self, *_args, **_kwargs):
            self._slots = []

        def connect(self, fn):
            self._slots.append(fn)

        def emit(self, *args, **kwargs):
            for fn in list(self._slots):
                fn(*args, **kwargs)

    class QObject:
        def __init__(self, *_args, **_kwargs):
            pass

    class QTimer:
        def __init__(self, *_args, **_kwargs):
            self.timeout = DummySignal()

        def setSingleShot(self, *_args):
            pass

        def start(self, *_args):
            pass

        def stop(self):
            pass

    qtcore = types.ModuleType("PySide6.QtCore")
    qtcore.Signal = DummySignal
    qtcore.QObject = QObject
    qtcore.QTimer = QTimer
    pyside = types.ModuleType("PySide6")
    pyside.QtCore = qtcore
    sys.modules["PySide6"] = pyside
    sys.modules["PySide6.QtCore"] = qtcore


_install_qt_stub_if_needed()

from devices.shimmer_device import ShimmerDevice  # noqa: E402
from models.enums import (  # noqa: E402
    DeviceStatus,
    Environment,
    FeedbackTiming,
    Modality,
    Study,
)
from models.trial import ExperimentCondition, Trial  # noqa: E402


def test_trial_scoped_shimmer_csv(tmp_path: Path) -> None:
    trial_dir = tmp_path / "trial"
    (trial_dir / "sensors").mkdir(parents=True)
    condition = ExperimentCondition(
        study=Study.STUDY_1,
        environment=Environment.GRIDWORLD,
        feedback_timing=FeedbackTiming.REQUESTED,
        modality=Modality.KEYBOARD,
    )
    trial = Trial(
        trial_id="T001",
        session_id="S001",
        participant_code="P001",
        condition=condition,
        practice=False,
        trial_dir=str(trial_dir),
    )
    trial.started_at = time.time() - 0.1

    device = ShimmerDevice(tmp_path)
    device._set_status(DeviceStatus.RECEIVING_DATA)
    device._sample_count = 10
    device._latest_packet_monotonic = time.monotonic()
    device._stream_started_monotonic = time.monotonic() - 1.0

    csv_path = device.start_trial_recording(trial)
    device._write_trial_sample(
        now_epoch=time.time(),
        now_mono=time.monotonic(),
        stream_sample_index=11,
        timestamp_raw=12345,
        timestamp_seconds=1.2345,
        values={"gsr_raw": 456, "ppg_raw": 789},
        gsr_adc=111,
        gsr_range=2,
    )
    summary = device.stop_trial_recording(trial.trial_id, "trial_completed")

    rows = list(csv.DictReader(csv_path.open(encoding="utf-8")))
    metadata = json.loads(
        (trial_dir / "sensors" / "shimmer_recording_metadata.json").read_text()
    )

    assert len(rows) == 1
    assert rows[0]["participant_code"] == "P001"
    assert rows[0]["trial_id"] == "T001"
    assert rows[0]["gsr_raw"] == "456"
    assert rows[0]["ppg_raw"] == "789"
    assert float(rows[0]["trial_elapsed_s"]) >= 0.0
    assert summary is not None and summary["sample_count"] == 1
    assert metadata["samples_saved"] == 1
    assert metadata["completion_reason"] == "trial_completed"
