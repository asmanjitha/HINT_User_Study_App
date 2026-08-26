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
