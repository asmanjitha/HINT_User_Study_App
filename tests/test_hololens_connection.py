"""Hardware-independent tests for the HoloLens 2 adapter helpers."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from devices.hololens_device import HoloLensDevice


def test_resolve_hl2ss_client_dir_accepts_root_or_viewer(tmp_path):
    viewer = tmp_path / "hl2ss" / "viewer"
    viewer.mkdir(parents=True)
    (viewer / "hl2ss.py").write_text("# fake")
    (viewer / "hl2ss_lnm.py").write_text("# fake")

    assert HoloLensDevice.resolve_client_dir(viewer) == viewer.resolve()
    assert HoloLensDevice.resolve_client_dir(viewer.parent) == viewer.resolve()
    assert HoloLensDevice.resolve_client_dir(tmp_path / "missing") is None


def test_extract_eye_packet_preserves_three_gaze_rays():
    def ray(origin, direction):
        return SimpleNamespace(origin=origin, direction=direction)

    payload = SimpleNamespace(
        calibration_valid=True,
        combined_ray_valid=True,
        combined_ray=ray((1, 2, 3), (0, 0, -1)),
        left_ray_valid=True,
        left_ray=ray((1.1, 2.1, 3.1), (-0.1, 0.0, -0.9)),
        right_ray_valid=False,
        right_ray=ray((0.9, 1.9, 2.9), (0.1, 0.0, -0.9)),
        left_openness_valid=False,
        left_openness=0.0,
        right_openness_valid=False,
        right_openness=0.0,
        vergence_distance_valid=False,
        vergence_distance=0.0,
    )
    packet = SimpleNamespace(timestamp=123456, pose=np.eye(4, dtype=np.float32), payload=payload)

    data = HoloLensDevice.extract_eye_packet(packet)
    assert data["timestamp"] == 123456
    assert np.allclose(data["pose"], np.eye(4))
    assert data["calibration_valid"] is True
    assert data["combined_valid"] is True
    assert data["combined"]["origin"] == (1.0, 2.0, 3.0)
    assert data["left"]["direction"] == (-0.1, 0.0, -0.9)
    assert data["right_valid"] is False


def test_project_combined_gaze_to_center_of_pv_image():
    # EET forward is -Z; the PV coordinate fix converts it to camera +Z.
    eye = {
        "timestamp": 1_000_000,
        "calibration_valid": True,
        "combined_valid": True,
        "combined": {"origin": (0.0, 0.0, 0.0), "direction": (0.0, 0.0, -1.0)},
        "pose": np.eye(4),
    }
    camera = {
        "timestamp": 1_010_000,
        "pose": np.eye(4),
        "focal_length": (500.0, 500.0),
        "principal_point": (640.0, 360.0),
        "width": 1280,
        "height": 720,
    }

    result = HoloLensDevice.project_gaze_to_pv(eye, camera, distance_m=1.5)
    assert result["valid"] is True
    assert result["in_frame"] is True
    assert np.allclose(result["pixel"], (640.0, 360.0))
    assert result["timestamp_delta_ms"] == 1.0


def test_nearest_eye_sample_uses_camera_timestamp():
    samples = [
        {"timestamp": 100},
        {"timestamp": 200},
        {"timestamp": 300},
    ]
    selected = HoloLensDevice._nearest_eye_sample(samples, 218)
    assert selected["timestamp"] == 200


def test_hololens_trial_recording_accepts_training_and_saves_under_run_folder(tmp_path, monkeypatch):
    import json
    from models.enums import Environment, FeedbackTiming, Modality, Study
    from models.trial import ExperimentCondition, Trial

    class FakeVideoWriter:
        def __init__(self, *args, **kwargs):
            self.frames = []
            self.released = False

        def isOpened(self):
            return True

        def write(self, frame):
            self.frames.append(frame.copy())

        def release(self):
            self.released = True

    fake_writer = FakeVideoWriter()
    monkeypatch.setattr("devices.hololens_device.cv2.VideoWriter", lambda *a, **k: fake_writer)
    monkeypatch.setattr("devices.hololens_device.cv2.VideoWriter_fourcc", lambda *a: 0)

    device = HoloLensDevice()
    device._width = 64
    device._height = 48
    device._camera_fps = 30
    device._eye_fps = 60
    monkeypatch.setattr(device, "is_stream_healthy", lambda max_age_s=1.0: True)

    run_dir = tmp_path / "P001" / "S01" / "Training" / "Study1" / "TR01_Gridworld_Anytime_Gaze" / "R01"
    trial = Trial(
        trial_id="P001_S01_ST1TR_TR01_R01",
        session_id="P001_S01",
        participant_code="P001",
        condition=ExperimentCondition(
            study=Study.STUDY_1,
            environment=Environment.GRIDWORLD,
            feedback_timing=FeedbackTiming.ANYTIME,
            modality=Modality.EYE_GAZE,
        ),
        practice=True,
        condition_code="TR01",
        run_code="R01",
        condition_name="Gridworld_Anytime_Gaze",
        started_at=1000.0,
        trial_dir=str(run_dir),
    )

    paths = device.start_trial_recording(trial)
    assert paths["video"] == run_dir / "sensors" / "hololens" / "pv_gaze.mp4"
    assert paths["pointer_csv"].name == "gaze.csv"
    assert paths["eet_csv"].name == "eet.csv"
    assert paths["metadata"].name == "meta.json"
    assert paths["pointer_csv"].exists()
    assert paths["eet_csv"].exists()

    eye = {
        "timestamp": 1_000_000,
        "pose": np.eye(4),
        "calibration_valid": True,
        "combined_valid": True,
        "combined": {"origin": (0.0, 0.0, 0.0), "direction": (0.0, 0.0, -1.0)},
        "left_valid": True,
        "left": {"origin": (-0.01, 0.0, 0.0), "direction": (0.0, 0.0, -1.0)},
        "right_valid": True,
        "right": {"origin": (0.01, 0.0, 0.0), "direction": (0.0, 0.0, -1.0)},
    }
    with device._lock:
        device._eye_history.append(eye)

    camera = {
        "timestamp": 1_000_000,
        "pose": np.eye(4),
        "focal_length": (40.0, 40.0),
        "principal_point": (32.0, 24.0),
        "width": 64,
        "height": 48,
    }
    frame = np.zeros((48, 64, 3), dtype=np.uint8)
    device._record_eet_sample(eye, host_monotonic=10.0)
    device._record_pv_frame(frame, camera, host_monotonic=10.1)
    summary = device.stop_trial_recording(trial_id=trial.trial_id, reason="trial_valid")

    assert summary["video_frame_count"] == 1
    assert summary["pointer_row_count"] == 1
    assert summary["eet_row_count"] == 1
    assert len(fake_writer.frames) == 1
    assert fake_writer.released is True
    # The overlay should modify at least a few pixels near the projected center.
    assert int(fake_writer.frames[0].sum()) > 0

    pointer_lines = paths["pointer_csv"].read_text(encoding="utf-8").strip().splitlines()
    eet_lines = paths["eet_csv"].read_text(encoding="utf-8").strip().splitlines()
    assert len(pointer_lines) == 2
    assert len(eet_lines) == 2

    metadata = json.loads(paths["metadata"].read_text(encoding="utf-8"))
    assert metadata["practice"] is True
    assert metadata["condition_code"] == "TR01"
    assert metadata["run_code"] == "R01"
    assert metadata["camera"]["video_frame_count"] == 1
    assert metadata["eye_tracking"]["eet_sample_count"] == 1
    assert metadata["stop_reason"] == "trial_valid"


def test_hololens_stop_is_idempotent_when_final_metadata_write_fails(tmp_path, monkeypatch):
    from models.enums import Environment, FeedbackTiming, Modality, Study
    from models.trial import ExperimentCondition, Trial

    class FakeVideoWriter:
        def __init__(self, *args, **kwargs):
            self.released = False

        def isOpened(self):
            return True

        def write(self, frame):
            pass

        def release(self):
            self.released = True

    fake_writer = FakeVideoWriter()
    monkeypatch.setattr("devices.hololens_device.cv2.VideoWriter", lambda *a, **k: fake_writer)
    monkeypatch.setattr("devices.hololens_device.cv2.VideoWriter_fourcc", lambda *a: 0)

    device = HoloLensDevice()
    device._width = 64
    device._height = 48
    monkeypatch.setattr(device, "is_stream_healthy", lambda max_age_s=1.0: True)

    run_dir = tmp_path / "P001" / "S01" / "Training" / "Study1" / "TR01_Gridworld_Requested_Gaze" / "R01"
    trial = Trial(
        trial_id="P001_S01_ST1TR_TR01_R01",
        session_id="P001_S01",
        participant_code="P001",
        condition=ExperimentCondition(
            study=Study.STUDY_1,
            environment=Environment.GRIDWORLD,
            feedback_timing=FeedbackTiming.REQUESTED,
            modality=Modality.EYE_GAZE,
        ),
        practice=True,
        condition_code="TR01",
        run_code="R01",
        condition_name="Gridworld_Requested_Gaze",
        started_at=1000.0,
        trial_dir=str(run_dir),
    )

    device.start_trial_recording(trial)
    rec = device._trial_recording
    assert rec is not None

    original = device._write_recording_metadata_for

    def fail_only_on_final(target, *, ended_at, reason):
        if ended_at is not None:
            raise FileNotFoundError("simulated final metadata path failure")
        return original(target, ended_at=ended_at, reason=reason)

    monkeypatch.setattr(device, "_write_recording_metadata_for", fail_only_on_final)
    summary = device.stop_trial_recording(trial_id=trial.trial_id, reason="trial_valid")

    assert summary is not None
    assert summary["metadata_finalized"] is False
    assert device._trial_recording is None
    assert rec.pointer_handle.closed is True
    assert rec.eet_handle.closed is True
    assert fake_writer.released is True
    # A second stop cannot re-close/re-flush the half-failed recorder.
    assert device.stop_trial_recording(trial_id=trial.trial_id, reason="again") is None

    # Worker-side record calls after finalization are harmless no-ops.
    eye = {
        "timestamp": 1,
        "calibration_valid": False,
        "combined_valid": False,
        "left_valid": False,
        "right_valid": False,
    }
    device._record_eet_sample(eye, host_monotonic=10.0)
