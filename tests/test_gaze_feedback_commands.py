"""Hardware-independent tests for HoloLens eye-gaze feedback gestures."""

from __future__ import annotations

import pytest

from devices.gaze_gesture_recognizer import (
    GAZE_CONTEXT_BLINK_COUNT,
    GAZE_CONTEXT_DIRECTION,
    GAZE_CONTEXT_DOUBLE_BLINK,
    GAZE_CONTEXT_LONG_CLOSE,
    EyeGazeGestureRecognizer,
)


class FakeHoloLens:
    def latest_eye_data(self):
        return {}


def eye_sample(ts: int, *, open_: bool = True, direction=(0.0, 0.0, -1.0), calibrated=True):
    return {
        "timestamp": ts,
        "calibration_valid": calibrated,
        "combined_valid": open_,
        "left_valid": open_,
        "right_valid": open_,
        "combined": {"direction": direction},
    }


def recognizer(**config):
    return EyeGazeGestureRecognizer(FakeHoloLens(), config=config)


def blink(rec, ts: int, start: float, duration: float = 0.12):
    rec.process_sample(eye_sample(ts, open_=False), now=start)
    rec.process_sample(eye_sample(ts + 1, open_=True), now=start + duration)


def test_double_blink_is_anytime_pause_command():
    rec = recognizer(double_blink_window_seconds=1.1)
    commands = []
    rec.command_recognized.connect(commands.append)
    rec.set_context(GAZE_CONTEXT_DOUBLE_BLINK)

    blink(rec, 1, 0.10)
    blink(rec, 3, 0.65)

    assert commands[-1]["gesture"] == "double_blink"
    assert commands[-1]["command"] == "pause"


def test_one_second_eye_close_starts_blink_count_stage():
    rec = recognizer(long_close_seconds=0.85)
    commands = []
    rec.command_recognized.connect(commands.append)
    rec.set_context(GAZE_CONTEXT_LONG_CLOSE)

    rec.process_sample(eye_sample(1, open_=False), now=1.00)
    rec.process_sample(eye_sample(2, open_=True), now=1.95)

    assert commands[-1]["gesture"] == "long_close"
    assert commands[-1]["command"] == "begin_blink_count"
    assert commands[-1]["duration_seconds"] == pytest.approx(0.95)


def test_n_blinks_select_state_number_after_open_gap():
    rec = recognizer(blink_count_finish_gap_seconds=1.0)
    commands = []
    rec.command_recognized.connect(commands.append)
    rec.set_context(GAZE_CONTEXT_BLINK_COUNT)

    blink(rec, 1, 0.10)
    blink(rec, 3, 0.50)
    blink(rec, 5, 0.90)
    rec.tick(now=2.10)

    assert commands[-1]["gesture"] == "blink_count"
    assert commands[-1]["count"] == 3
    assert commands[-1]["command"] == "3"


@pytest.mark.parametrize(
    ("label", "direction"),
    [
        ("left", (-0.35, 0.0, -1.0)),
        ("right", (0.35, 0.0, -1.0)),
        ("up", (0.0, 0.35, -1.0)),
        ("down", (0.0, -0.35, -1.0)),
    ],
)
def test_windowed_likelihood_becomes_direction_command(label, direction):
    rec = recognizer(
        direction_threshold_deg=12.0,
        direction_neutral_deg=6.0,
        direction_window_seconds=0.70,
        direction_min_valid_samples=5,
        direction_probability_threshold=0.70,
        direction_probability_margin=0.20,
    )
    commands = []
    rec.command_recognized.connect(commands.append)
    rec.set_context(GAZE_CONTEXT_DIRECTION)

    # First sample establishes the local center. Five clear valid samples are
    # enough evidence even though no uninterrupted hold duration is required.
    rec.process_sample(eye_sample(1, direction=(0.0, 0.0, -1.0)), now=0.05)
    for index, now in enumerate((0.10, 0.18, 0.27, 0.36, 0.47), start=2):
        rec.process_sample(eye_sample(index, direction=direction), now=now)

    assert commands[-1]["gesture"] == "windowed_direction"
    assert commands[-1]["command"] == label
    assert commands[-1]["valid_samples"] >= 5
    assert commands[-1]["confidence"] >= 0.70


def test_missing_gaze_samples_do_not_reset_direction_evidence():
    rec = recognizer(
        direction_window_seconds=0.70,
        direction_min_valid_samples=5,
        direction_probability_threshold=0.70,
        direction_probability_margin=0.20,
    )
    commands = []
    rec.command_recognized.connect(commands.append)
    rec.set_context(GAZE_CONTEXT_DIRECTION)

    center = (0.0, 0.0, -1.0)
    left = (-0.35, 0.0, -1.0)
    rec.process_sample(eye_sample(1, direction=center), now=0.00)

    # Simulate intermittent EET loss between every usable gaze packet. Invalid
    # packets are ignored and do not discard the accumulated LEFT evidence.
    ts = 2
    for now in (0.10, 0.20, 0.30, 0.40, 0.50):
        rec.process_sample(eye_sample(ts, direction=left), now=now)
        ts += 1
        rec.process_sample(eye_sample(ts, open_=False), now=now + 0.04)
        ts += 1

    assert len(commands) == 1
    assert commands[0]["command"] == "left"
    assert commands[0]["valid_samples"] == 5


def test_missing_samples_do_not_count_toward_minimum_valid_samples():
    rec = recognizer(direction_min_valid_samples=5)
    commands = []
    rec.command_recognized.connect(commands.append)
    rec.set_context(GAZE_CONTEXT_DIRECTION)

    center = (0.0, 0.0, -1.0)
    right = (0.35, 0.0, -1.0)
    rec.process_sample(eye_sample(1, direction=center), now=0.00)
    rec.process_sample(eye_sample(2, direction=right), now=0.10)
    rec.process_sample(eye_sample(3, open_=False), now=0.15)
    rec.process_sample(eye_sample(4, open_=False), now=0.20)
    rec.process_sample(eye_sample(5, direction=right), now=0.25)
    rec.process_sample(eye_sample(6, direction=right), now=0.35)

    assert commands == []


def test_single_direction_glance_followed_by_center_is_not_accepted():
    rec = recognizer(
        direction_min_valid_samples=5,
        direction_probability_threshold=0.70,
    )
    commands = []
    rec.command_recognized.connect(commands.append)
    rec.set_context(GAZE_CONTEXT_DIRECTION)

    center = (0.0, 0.0, -1.0)
    left = (-0.35, 0.0, -1.0)
    rec.process_sample(eye_sample(1, direction=center), now=0.00)
    rec.process_sample(eye_sample(2, direction=left), now=0.10)
    for ts, now in enumerate((0.20, 0.30, 0.40, 0.50), start=3):
        rec.process_sample(eye_sample(ts, direction=center), now=now)

    assert commands == []


def test_diagonal_gaze_is_ambiguous_and_not_accepted():
    rec = recognizer(
        direction_min_valid_samples=5,
        direction_probability_threshold=0.70,
        direction_probability_margin=0.20,
    )
    commands = []
    rec.command_recognized.connect(commands.append)
    rec.set_context(GAZE_CONTEXT_DIRECTION)

    rec.process_sample(eye_sample(1, direction=(0.0, 0.0, -1.0)), now=0.00)
    diagonal = (0.28, 0.28, -1.0)
    for ts, now in enumerate((0.10, 0.20, 0.30, 0.40, 0.50), start=2):
        rec.process_sample(eye_sample(ts, direction=diagonal), now=now)

    assert commands == []


def test_windowed_direction_emits_only_once():
    rec = recognizer(direction_min_valid_samples=5)
    commands = []
    rec.command_recognized.connect(commands.append)
    rec.set_context(GAZE_CONTEXT_DIRECTION)

    center = (0.0, 0.0, -1.0)
    left = (-0.35, 0.0, -1.0)
    rec.process_sample(eye_sample(1, direction=center), now=0.00)
    for ts, now in enumerate((0.10, 0.18, 0.26, 0.34, 0.42, 0.50, 0.58), start=2):
        rec.process_sample(eye_sample(ts, direction=left), now=now)

    assert len(commands) == 1
    assert commands[0]["command"] == "left"



def test_old_direction_evidence_expires_outside_window():
    rec = recognizer(direction_window_seconds=0.70, direction_min_valid_samples=5)
    commands = []
    rec.command_recognized.connect(commands.append)
    rec.set_context(GAZE_CONTEXT_DIRECTION)

    center = (0.0, 0.0, -1.0)
    left = (-0.35, 0.0, -1.0)
    rec.process_sample(eye_sample(1, direction=center), now=0.00)
    rec.process_sample(eye_sample(2, direction=left), now=0.10)

    # The old LEFT packet has expired by the time the next burst arrives, so the
    # four new valid packets are still below the minimum sample requirement.
    for ts, now in enumerate((0.90, 0.98, 1.06, 1.14), start=3):
        rec.process_sample(eye_sample(ts, direction=left), now=now)

    assert commands == []


def test_uncalibrated_tracking_loss_is_not_treated_as_blink():
    rec = recognizer()
    commands = []
    rec.command_recognized.connect(commands.append)
    rec.set_context(GAZE_CONTEXT_DOUBLE_BLINK)

    rec.process_sample(eye_sample(1, open_=False, calibrated=False), now=0.1)
    rec.process_sample(eye_sample(2, open_=True, calibrated=True), now=0.2)
    rec.process_sample(eye_sample(3, open_=False, calibrated=False), now=0.5)
    rec.process_sample(eye_sample(4, open_=True, calibrated=True), now=0.6)

    assert commands == []


def test_direction_debug_reports_angles_labels_and_rolling_probabilities():
    rec = recognizer(
        direction_debug_enabled=True,
        direction_min_valid_samples=5,
    )
    debug = []
    rec.direction_debug.connect(debug.append)
    rec.set_context(GAZE_CONTEXT_DIRECTION)

    rec.process_sample(eye_sample(1, direction=(0.0, 0.0, -1.0)), now=0.00)
    rec.process_sample(eye_sample(2, direction=(-0.35, 0.0, -1.0)), now=0.10)

    assert debug[0]["status"] == "center_set"
    evidence = debug[-1]
    assert evidence["status"] == "valid"
    assert evidence["instant_direction"] == "left"
    assert evidence["delta_horizontal_deg"] < -12.0
    assert evidence["rolling_direction"] == "left"
    assert evidence["prob_left"] > evidence["prob_right"]
    assert evidence["valid_samples"] == 1



def test_positive_z_hololens_forward_gaze_is_accepted_and_right_is_recognized():
    """Regression for the real HL2SS stream where forward gaze has +Z."""
    rec = recognizer(
        direction_threshold_deg=12.0,
        direction_neutral_deg=6.0,
        direction_window_seconds=0.70,
        direction_min_valid_samples=5,
        direction_probability_threshold=0.70,
        direction_probability_margin=0.20,
        direction_debug_enabled=True,
    )
    commands = []
    debug = []
    rec.command_recognized.connect(commands.append)
    rec.direction_debug.connect(debug.append)
    rec.set_context(GAZE_CONTEXT_DIRECTION)

    # Real study logs show forward-looking HoloLens EET rays with positive Z.
    rec.process_sample(eye_sample(1, direction=(0.0, 0.0, 1.0)), now=0.00)
    right = (0.35, 0.0, 1.0)
    for ts, now in enumerate((0.10, 0.18, 0.27, 0.36, 0.47), start=2):
        rec.process_sample(eye_sample(ts, direction=right), now=now)

    assert debug[0]["status"] == "center_set"
    assert all(item.get("reason") != "gaze_not_forward" for item in debug)
    assert commands[-1]["command"] == "right"
    assert commands[-1]["valid_samples"] >= 5


def test_positive_z_debug_reports_relative_right_angle():
    rec = recognizer(direction_debug_enabled=True, direction_min_valid_samples=5)
    debug = []
    rec.direction_debug.connect(debug.append)
    rec.set_context(GAZE_CONTEXT_DIRECTION)

    rec.process_sample(eye_sample(1, direction=(0.0, 0.0, 1.0)), now=0.00)
    rec.process_sample(eye_sample(2, direction=(0.25, 0.0, 1.0)), now=0.10)

    assert debug[0]["status"] == "center_set"
    evidence = debug[-1]
    assert evidence["status"] == "valid"
    assert evidence["delta_horizontal_deg"] > 12.0
    assert evidence["instant_direction"] == "right"

def test_direction_debug_reports_invalid_combined_gaze():
    rec = recognizer(direction_debug_enabled=True)
    debug = []
    rec.direction_debug.connect(debug.append)
    rec.set_context(GAZE_CONTEXT_DIRECTION)

    # Calibration is valid and one eye is valid, but the combined gaze ray is not.
    sample = eye_sample(1, open_=True)
    sample["combined_valid"] = False
    sample["left_valid"] = True
    sample["right_valid"] = False
    rec.process_sample(sample, now=0.10)

    assert debug[-1]["status"] == "invalid"
    assert debug[-1]["reason"] == "combined_gaze_invalid"
    assert debug[-1]["left_valid"] is True
