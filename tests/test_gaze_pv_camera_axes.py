"""Regression tests for EET tracker-space -> PV camera gaze direction mapping."""

from __future__ import annotations

import numpy as np
import pytest

from devices.hololens_device import HoloLensDevice


def _eye(direction, pose=None):
    return {
        "combined_valid": True,
        "combined": {"origin": (0.0, 0.0, 0.0), "direction": direction},
        "pose": np.eye(4) if pose is None else pose,
    }


def _camera(pose=None):
    return {"pose": np.eye(4) if pose is None else pose}


@pytest.mark.parametrize(
    ("name", "tracker_direction", "axis", "sign"),
    [
        ("right", (0.35, 0.0, -1.0), 0, +1),
        ("left", (-0.35, 0.0, -1.0), 0, -1),
        ("up", (0.0, 0.35, -1.0), 1, -1),
        ("down", (0.0, -0.35, -1.0), 1, +1),
    ],
)
def test_identity_tracker_pose_maps_physical_axes_to_pv_camera(name, tracker_direction, axis, sign):
    direction = HoloLensDevice.gaze_direction_to_pv_camera(
        _eye(tracker_direction), _camera()
    )
    assert direction is not None, name
    assert direction[2] > 0.0
    assert direction[axis] * sign > 0.1


def test_tracker_axes_are_not_assumed_to_equal_camera_axes():
    """A rotated tracker pose must rotate gaze into camera axes before classification."""
    # Row-vector rotation: tracker -Y maps to camera/world +X, while tracker -Z
    # remains camera/world forward (-Z before the PV Y/Z convention flip).
    # This mimics the real failure where a horizontal gaze appeared mostly in a
    # different raw tracker component.
    eye_pose = np.eye(4)
    eye_pose[:3, :3] = np.array(
        [
            [0.0, 1.0, 0.0],
            [-1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0],
        ],
        dtype=float,
    )

    # Raw tracker -Y is transformed to camera +X => RIGHT.
    direction = HoloLensDevice.gaze_direction_to_pv_camera(
        _eye((0.0, -0.35, -1.0), pose=eye_pose), _camera()
    )
    assert direction is not None
    assert direction[0] > 0.1
    assert abs(direction[1]) < 0.1
    assert direction[2] > 0.0
