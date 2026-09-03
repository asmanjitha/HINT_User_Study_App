"""Hardware-independent tests for Beam extraction, recording, and routing."""

from __future__ import annotations

import csv
import json
import time
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

from core.sensor_policy import eye_tracker_for_trial
from devices.beam_eye_tracker_device import BeamEyeTrackerDevice
from models.enums import DeviceType, Environment, FeedbackTiming, Modality, Study
from models.trial import ExperimentCondition, Trial


class _EnumValue:
    def __init__(self, name: str, value: int):
        self.name = name
        self.value = value


class _TrackingStateSet:
    def user_state(self):
        return SimpleNamespace(
            timestamp_in_seconds=12.5,
            unified_screen_gaze=SimpleNamespace(
                confidence=_EnumValue("HIGH", 3),
                point_of_regard=SimpleNamespace(x=800, y=450),
                unbounded_point_of_regard=SimpleNamespace(x=805, y=452),
            ),
            viewport_gaze=SimpleNamespace(
                confidence=_EnumValue("HIGH", 3),
                normalized_point_of_regard=SimpleNamespace(x=0.5, y=0.5),
            ),
            head_pose=SimpleNamespace(
                confidence=_EnumValue("MEDIUM", 2),
                track_session_uid=91,
                translation_from_hcs_to_wcs=SimpleNamespace(x=0.1, y=0.2, z=0.7),
                rotation_from_hcs_to_wcs=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
            ),
        )


def _trial(tmp_path: Path, study: Study, *, practice: bool = False) -> Trial:
    trial_dir = tmp_path / study.name
    (trial_dir / "sensors").mkdir(parents=True, exist_ok=True)
    trial = Trial(
        trial_id=f"TRIAL_{study.name}",
        session_id="P001_S01",
        participant_code="P001",
        condition=ExperimentCondition(
            study=study,
            environment=Environment.GRIDWORLD,
            feedback_timing=(
                FeedbackTiming.NOT_APPLICABLE
                if study == Study.OBSERVATION
                else FeedbackTiming.REQUESTED
            ),
            modality=Modality.NONE if study == Study.OBSERVATION else Modality.KEYBOARD,
        ),
        practice=practice,
        trial_dir=str(trial_dir),
    )
    trial.started_at = time.time()
    return trial


def test_beam_extracts_official_sdk_user_state_fields() -> None:
    sample = BeamEyeTrackerDevice.extract_sample(_TrackingStateSet())
    assert sample["beam_timestamp_seconds"] == 12.5
    assert sample["screen_gaze_x_px"] == 800.0
    assert sample["viewport_gaze_y_normalized"] == 0.5
    assert sample["gaze_confidence"] == "HIGH"
    assert sample["viewport_gaze_confidence"] == "HIGH"
    assert sample["head_track_session_uid"] == 91.0
    assert sample["valid"] == 1


def test_beam_trial_recording_writes_csv_and_metadata(tmp_path: Path) -> None:
    device = BeamEyeTrackerDevice(screen_recording_config={"enabled": False})
    device.set_capture_target(
        1920,
        0,
        1920,
        1080,
        auto_follow_participant_window=False,
    )
    device._reception_status = "RECEIVING_TRACKING_DATA"
    device._last_sample_monotonic = time.monotonic()
    trial = _trial(tmp_path, Study.STUDY_1)

    paths = device.start_trial_recording(trial)
    device._accept_sample(BeamEyeTrackerDevice.extract_sample(_TrackingStateSet()))
    summary = device.stop_trial_recording(trial.trial_id, "trial_valid")

    rows = list(csv.DictReader(paths["gaze_csv"].open(encoding="utf-8")))
    metadata = json.loads(paths["metadata"].read_text(encoding="utf-8"))
    assert len(rows) == 1
    assert rows[0]["screen_gaze_x_px"] == "800.0"
    assert rows[0]["valid"] == "1"
    assert float(rows[0]["trial_elapsed_seconds"]) >= 0
    assert summary is not None and summary["sample_count"] == 1
    assert summary["valid_sample_count"] == 1
    assert metadata["sensor"] == "Beam Eye Tracker"
    assert metadata["stop_reason"] == "trial_valid"
    assert metadata["webcam_video_recorded"] is False
    assert metadata["viewport_x_y_width_height"] == [1920, 0, 1920, 1080]
    assert metadata["capture_target_mode"] == "manual_display"
    assert metadata["capture_target_source"] == "manual_display"


def test_beam_matches_participant_window_to_physical_display() -> None:
    displays = [
        {"index": 1, "name": "Display 1", "x": 0, "y": 0, "width": 1920, "height": 1080},
        {"index": 2, "name": "Display 2", "x": 1920, "y": 0, "width": 2560, "height": 1440},
    ]
    matched = BeamEyeTrackerDevice.match_display_to_rect(
        (2100, 120, 1000, 800), displays
    )
    assert matched is not None
    assert matched["name"] == "Display 2"


def test_beam_automatic_target_follows_participant_window(monkeypatch) -> None:
    device = BeamEyeTrackerDevice(screen_recording_config={"enabled": False})
    device.set_capture_target(
        0,
        0,
        1920,
        1080,
        auto_follow_participant_window=True,
    )
    displays = [
        {"index": 1, "name": "Display 1", "x": 0, "y": 0, "width": 1920, "height": 1080},
        {"index": 2, "name": "Display 2", "x": 1920, "y": 0, "width": 2560, "height": 1440},
    ]
    monkeypatch.setattr(
        device,
        "_windows_monitor_rect_for_window",
        lambda _hwnd: (1920, 0, 2560, 1440),
    )
    monkeypatch.setattr(device, "available_displays", lambda: displays)

    ok, message = device.sync_capture_to_participant_window(12345)
    stats = device.stats()

    assert ok is True
    assert "Display 2" in message
    assert stats["viewport"] == (1920, 0, 2560, 1440)
    assert stats["manual_fallback_viewport"] == (0, 0, 1920, 1080)
    assert stats["capture_target_mode"] == "automatic_participant_window"
    assert stats["capture_target_source"] == "participant_window_Display 2"


def test_beam_manual_target_does_not_follow_participant_window(monkeypatch) -> None:
    device = BeamEyeTrackerDevice(screen_recording_config={"enabled": False})
    device.set_capture_target(
        0,
        0,
        1920,
        1080,
        auto_follow_participant_window=False,
    )
    monkeypatch.setattr(
        device,
        "_windows_monitor_rect_for_window",
        lambda _hwnd: (1920, 0, 2560, 1440),
    )

    ok, _message = device.sync_capture_to_participant_window(12345)
    assert ok is True
    assert device.stats()["viewport"] == (0, 0, 1920, 1080)


def test_gaze_overlay_maps_normalized_point_to_screen_frame() -> None:
    frame = np.zeros((100, 200, 3), dtype=np.uint8)
    sample = {
        "viewport_gaze_x_normalized": 0.50,
        "viewport_gaze_y_normalized": 0.25,
        "viewport_gaze_confidence": "HIGH",
        "valid": 1,
    }
    drawn = BeamEyeTrackerDevice.render_gaze_overlay(
        frame,
        sample,
        viewport=(1920, 0, 200, 100),
        sample_age_seconds=0.01,
        radius_px=10,
        show_status=False,
        cv2_module=cv2,
    )
    assert drawn is True
    # Expected local point is approximately (100, 25), regardless of the
    # selected monitor's global desktop offset.
    assert np.any(frame[12:39, 86:114] != 0)


def test_gaze_overlay_hides_lost_or_stale_pointer() -> None:
    frame = np.zeros((100, 200, 3), dtype=np.uint8)
    sample = {
        "viewport_gaze_x_normalized": 0.5,
        "viewport_gaze_y_normalized": 0.5,
        "viewport_gaze_confidence": "LOST_TRACKING",
        "valid": 0,
    }
    drawn = BeamEyeTrackerDevice.render_gaze_overlay(
        frame,
        sample,
        viewport=(0, 0, 200, 100),
        sample_age_seconds=0.01,
        show_status=False,
        cv2_module=cv2,
    )
    assert drawn is False
    assert not np.any(frame)


def test_mp4_writer_accepts_gaze_overlay_frames(tmp_path: Path) -> None:
    device = BeamEyeTrackerDevice(
        screen_recording_config={"enabled": True, "fps": 10, "codec": "mp4v"}
    )
    video_path = tmp_path / "screen_gaze.mp4"
    writer = device._open_screen_video_writer(video_path, 160, 90)
    for frame_index in range(5):
        frame = np.zeros((90, 160, 3), dtype=np.uint8)
        BeamEyeTrackerDevice.render_gaze_overlay(
            frame,
            {
                "viewport_gaze_x_normalized": 0.2 + frame_index * 0.1,
                "viewport_gaze_y_normalized": 0.5,
                "viewport_gaze_confidence": "HIGH",
                "valid": 1,
            },
            viewport=(0, 0, 160, 90),
            sample_age_seconds=0.01,
            show_status=True,
            elapsed_seconds=frame_index / 10,
            cv2_module=cv2,
        )
        writer.write(frame)
    writer.release()

    assert video_path.exists() and video_path.stat().st_size > 0
    capture = cv2.VideoCapture(str(video_path))
    assert capture.isOpened()
    assert int(capture.get(cv2.CAP_PROP_FRAME_COUNT)) == 5
    ok, decoded = capture.read()
    capture.release()
    assert ok and decoded is not None and np.any(decoded)


def test_eye_tracker_policy_routes_only_observation_to_hololens(tmp_path: Path) -> None:
    assert eye_tracker_for_trial(_trial(tmp_path, Study.STUDY_1)) == DeviceType.BEAM
    assert eye_tracker_for_trial(_trial(tmp_path, Study.STUDY_2)) == DeviceType.BEAM
    assert eye_tracker_for_trial(_trial(tmp_path, Study.OBSERVATION)) == DeviceType.HOLOLENS
    assert eye_tracker_for_trial(
        _trial(tmp_path, Study.OBSERVATION, practice=True)
    ) == DeviceType.HOLOLENS
