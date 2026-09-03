"""Enumerations shared across the HINT Study Console.

Keeping these centralized avoids "magic strings" scattered across GUI,
device, and RL code, and gives us one place to update if the study protocol
changes.
"""

from __future__ import annotations

from enum import Enum


class AppMode(str, Enum):
    """Global application mode.

    STUDY mode locks experimental configuration between participants
    (see spec section 29). DEVELOPMENT mode allows free editing and
    bypasses hard session/task time limits.
    """

    DEVELOPMENT = "DEVELOPMENT"
    STUDY = "STUDY"


class DeviceStatus(str, Enum):
    DISCONNECTED = "Disconnected"
    CONNECTING = "Connecting"
    CONNECTED = "Connected"
    RECEIVING_DATA = "Receiving Data"
    WARNING = "Warning"
    ERROR = "Error"


class DeviceType(str, Enum):
    BEAM = "Beam Eye Tracker"
    HOLOLENS = "HoloLens"
    SHIMMER = "Shimmer"
    JOYSTICK = "Joystick"
    KEYBOARD = "Keyboard"
    MICROPHONE = "Microphone"


class SessionStatus(str, Enum):
    CREATED = "Created"
    IN_PROGRESS = "In Progress"
    PAUSED = "Paused"
    COMPLETED = "Completed"
    ABORTED = "Aborted"


class TrialStatus(str, Enum):
    CREATED = "Created"
    PRACTICE = "Practice"
    RUNNING = "Running"
    PAUSED = "Paused"
    STOPPED = "Stopped"
    COMPLETED = "Completed"


class CollectionRunStatus(str, Enum):
    """Research-validity status for one concrete R## collection attempt.

    This is intentionally separate from TrialStatus, which describes the
    execution lifecycle. A run may finish normally but still be INVALID for
    analysis and then be repeated as R02.
    """

    PENDING = "Pending"
    VALID = "Valid"
    INVALID = "Invalid"
    ABORTED = "Aborted"


class Study(str, Enum):
    STUDY_1 = "Study 1"
    STUDY_2 = "Study 2"
    OBSERVATION = "Agent Observation"
    # Kept at its historical stored value for database compatibility. A new
    # participant collection session can also contain Agent Observation trials.
    COMBINED_SESSION = "Study 1 + Study 2"


class Environment(str, Enum):
    GRIDWORLD = "Gridworld"
    CONTINUOUS_ROOM = "Continuous Room Navigation"
    HUMAN_AGENT_BASELINE = "Human-Agent Navigation (Baseline)"


class FeedbackTiming(str, Enum):
    REQUESTED = "Requested Feedback"
    ANYTIME = "Anytime Feedback"
    NOT_APPLICABLE = "N/A"


class Modality(str, Enum):
    KEYBOARD = "Keyboard"
    JOYSTICK = "Joystick"
    VOICE = "Voice"
    EYE_GAZE = "Eye Gaze"

    # Both labels are retained because older training data used EYE_GAZE,
    # while the current Study 2 IRB wording groups gaze + physiology as implicit.
    IMPLICIT = "Implicit (Gaze/Physiological)"
    NONE = "N/A (No Participant Feedback)"


class WorkflowStep(str, Enum):
    """Ordered steps in the participant's journey through the console.

    Registration happens once. Training and Study steps may be repeated
    (e.g. a participant redoes training, or does the HIL-RL study more
    than once) -- each repeat is a new "run" of that step.
    """

    REGISTRATION = "Registration"
    STUDY1_TRAINING = "Study 1 - Training"
    STUDY1_STUDY = "Study 1 - Study"
    STUDY2_TRAINING = "Study 2 - Training"
    STUDY2_STUDY = "Study 2 - Study"
    AGENT_OBSERVATION = "Agent Observation Phase"


class StepRunStatus(str, Enum):
    """Status of a single GUI workflow attempt."""

    IN_PROGRESS = "In Progress"
    COMPLETED = "Completed"
    INVALID = "Invalid"
    ABORTED = "Aborted"


class StepOverallStatus(str, Enum):
    """Aggregate status of a step across all of a participant's runs."""

    NOT_STARTED = "Not Started"
    IN_PROGRESS = "In Progress"
    COMPLETED = "Completed"


class EventType(str, Enum):
    """Canonical event types written to events.csv (spec section 14)."""

    APP_STARTED = "APP_STARTED"

    SESSION_CREATED = "SESSION_CREATED"
    SESSION_STARTED = "SESSION_STARTED"
    SESSION_ENDED = "SESSION_ENDED"

    PRACTICE_STARTED = "PRACTICE_STARTED"
    PRACTICE_ENDED = "PRACTICE_ENDED"

    TRIAL_CREATED = "TRIAL_CREATED"
    ACTIVITY_PREPARED = "ACTIVITY_PREPARED"
    PARTICIPANT_ACTIVITY_STARTED = "PARTICIPANT_ACTIVITY_STARTED"
    TRIAL_STARTED = "TRIAL_STARTED"
    TRIAL_PAUSED = "TRIAL_PAUSED"
    TRIAL_RESUMED = "TRIAL_RESUMED"
    TRIAL_ENDED = "TRIAL_ENDED"
    TRIAL_TIME_LIMIT_REACHED = "TRIAL_TIME_LIMIT_REACHED"
    WORKFLOW_COMPLETION_OVERRIDDEN = "WORKFLOW_COMPLETION_OVERRIDDEN"

    OBSERVATION_VIDEO_STARTED = "OBSERVATION_VIDEO_STARTED"
    OBSERVATION_VIDEO_ENDED = "OBSERVATION_VIDEO_ENDED"
    OBSERVATION_VIDEO_ERROR = "OBSERVATION_VIDEO_ERROR"

    EPISODE_STARTED = "EPISODE_STARTED"
    EPISODE_ENDED = "EPISODE_ENDED"

    CRITICAL_STATE = "CRITICAL_STATE"
    FEEDBACK_REQUESTED = "FEEDBACK_REQUESTED"
    ANYTIME_FEEDBACK_STARTED = "ANYTIME_FEEDBACK_STARTED"
    FEEDBACK_RECEIVED = "FEEDBACK_RECEIVED"
    FEEDBACK_SKIPPED = "FEEDBACK_SKIPPED"
    FEEDBACK_APPLIED = "FEEDBACK_APPLIED"

    VOICE_TRANSCRIPT = "VOICE_TRANSCRIPT"
    VOICE_COMMAND = "VOICE_COMMAND"
    GAZE_GESTURE = "GAZE_GESTURE"
    GAZE_COMMAND = "GAZE_COMMAND"
    GAZE_DIRECTION_DEBUG = "GAZE_DIRECTION_DEBUG"

    COLLISION = "COLLISION"
    GOAL_REACHED = "GOAL_REACHED"

    DEVICE_CONNECTED = "DEVICE_CONNECTED"
    DEVICE_DISCONNECTED = "DEVICE_DISCONNECTED"
    DEVICE_ERROR = "DEVICE_ERROR"
    DEVICE_RECONNECTED = "DEVICE_RECONNECTED"

    RECORDING_STARTED = "RECORDING_STARTED"
    RECORDING_STOPPED = "RECORDING_STOPPED"

    EMERGENCY_STOP = "EMERGENCY_STOP"

    EXPERIMENTER_NOTE = "EXPERIMENTER_NOTE"
