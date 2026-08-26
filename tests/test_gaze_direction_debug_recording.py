from __future__ import annotations

import csv

from core.event_bus import EventBus
from models.enums import Environment, EventType, FeedbackTiming, Modality, Study
from models.event import StudyEvent
from models.trial import ExperimentCondition, Trial
from recording.rl_trial_recorder import RLTrialRecorder


def test_gaze_direction_debug_event_writes_dedicated_csv(tmp_path):
    trial = Trial(
        trial_id="trial-1",
        session_id="S01",
        participant_code="P001",
        condition=ExperimentCondition(
            study=Study.STUDY_1,
            environment=Environment.GRIDWORLD,
            feedback_timing=FeedbackTiming.REQUESTED,
            modality=Modality.EYE_GAZE,
        ),
        trial_dir=str(tmp_path),
    )
    bus = EventBus()
    recorder = RLTrialRecorder(trial, bus)

    bus.publish(
        StudyEvent(
            event_type=EventType.GAZE_DIRECTION_DEBUG,
            participant_id="P001",
            session_id="S01",
            trial_id="trial-1",
            value=(
                "status=valid;reason=direction_evidence;timestamp=12345;"
                "delta_horizontal_deg=-14.2;delta_vertical_deg=2.1;"
                "instant_direction=left;rolling_direction=left;"
                "rolling_confidence=0.82;rolling_margin=0.61;"
                "prob_left=0.82;prob_right=0.01;prob_up=0.05;"
                "prob_down=0.02;prob_center=0.10;valid_samples=5;required_samples=5"
            ),
        )
    )
    recorder.close()

    path = tmp_path / "sensors" / "hololens" / "gaze_direction_debug.csv"
    assert path.exists()
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["eye_timestamp"] == "12345"
    assert rows[0]["instant_direction"] == "left"
    assert rows[0]["rolling_direction"] == "left"
    assert rows[0]["delta_horizontal_deg"] == "-14.2"
    assert rows[0]["prob_left"] == "0.82"
