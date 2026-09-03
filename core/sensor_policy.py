"""Central sensor-routing policy for HINT study activities."""

from __future__ import annotations

from models.enums import DeviceType, Study
from models.trial import Trial


def eye_tracker_for_trial(trial: Trial) -> DeviceType:
    """Return the eye tracker assigned by protocol to this activity."""
    if trial.condition.study == Study.OBSERVATION:
        return DeviceType.HOLOLENS
    return DeviceType.BEAM
