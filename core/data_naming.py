"""Human-readable, stable data-folder naming for HINT study collections.

The on-disk hierarchy is intentionally separate from the immutable database
identifiers.  A participant/session/study condition is stored as::

    P001/S01/Study1_ExplicitFeedback/
        T02_Gridworld_Anytime_Joystick/R01/

Repeating the exact same condition reuses T02 and creates R02, R03, ... .
Training/familiarization trials use TR## under Training/Study1 or
Training/Study2.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from core.database import Database
from models.enums import Environment, FeedbackTiming, Modality, Study
from models.trial import ExperimentCondition

_CONDITION_CODE_RE = re.compile(r"^(?:T|TR)(\d{2,})$")
_RUN_CODE_RE = re.compile(r"^R(\d{2,})$")
_SESSION_SHORT_RE = re.compile(r"^[^_]+_(S\d{2,})$")


STUDY_FOLDER = {
    Study.STUDY_1: "Study1_ExplicitFeedback",
    Study.STUDY_2: "Study2_FeedbackModality",
    Study.OBSERVATION: "Phase3_AgentObservation_NoFeedback",
}

TRAINING_STUDY_FOLDER = {
    Study.STUDY_1: "Study1",
    Study.STUDY_2: "Study2",
    Study.OBSERVATION: "Optional_HoloLens",
}

ENVIRONMENT_TOKEN = {
    Environment.GRIDWORLD: "Gridworld",
    Environment.CONTINUOUS_ROOM: "Room",
    Environment.HUMAN_AGENT_BASELINE: "Baseline",
}

TIMING_TOKEN = {
    FeedbackTiming.REQUESTED: "Requested",
    FeedbackTiming.ANYTIME: "Anytime",
    FeedbackTiming.NOT_APPLICABLE: "",
}

MODALITY_TOKEN = {
    Modality.KEYBOARD: "Keyboard",
    Modality.JOYSTICK: "Joystick",
    Modality.VOICE: "Voice",
    Modality.EYE_GAZE: "Gaze",
    Modality.IMPLICIT: "Implicit_GazePhysio",
    Modality.NONE: "NoFeedback",
}


@dataclass(frozen=True)
class TrialStorageIdentity:
    """Stable condition identity plus one concrete collection attempt."""

    condition_code: str
    run_code: str
    condition_name: str
    trial_id: str
    relative_dir: Path

    @property
    def condition_folder_name(self) -> str:
        return f"{self.condition_code}_{self.condition_name}"


def session_short_code(session_id: str) -> str:
    """Return S01 from the internal session id P001_S01."""
    match = _SESSION_SHORT_RE.match(session_id)
    if match:
        return match.group(1)
    # Graceful fallback for legacy or hand-created session ids.
    tail = session_id.rsplit("_", 1)[-1]
    return tail if tail.startswith("S") else session_id


def condition_name(condition: ExperimentCondition) -> str:
    """Readable condition name using a controlled vocabulary."""
    parts = [ENVIRONMENT_TOKEN[condition.environment]]
    timing = TIMING_TOKEN[condition.feedback_timing]
    if timing:
        parts.append(timing)
    parts.append(MODALITY_TOKEN[condition.modality])
    return "_".join(parts)


def condition_signature(condition: ExperimentCondition) -> tuple[str, str, str, str]:
    """Exact identity used to decide whether two attempts share one T##."""
    return (
        condition.study.value,
        condition.environment.value,
        condition.feedback_timing.value,
        condition.modality.value,
    )


def study_relative_root(study: Study, practice: bool) -> Path:
    if practice:
        return Path("Training") / TRAINING_STUDY_FOLDER[study]
    return Path(STUDY_FOLDER[study])


def _condition_rows(
    db: Database,
    *,
    session_id: str,
    condition: ExperimentCondition,
    practice: bool,
):
    return db.experimental_conn.execute(
        """
        SELECT trial_id, condition_code, run_code, condition_name,
               study, environment, feedback_timing, modality, practice, created_at
        FROM trials
        WHERE session_id = ?
          AND study = ?
          AND environment = ?
          AND feedback_timing = ?
          AND modality = ?
          AND practice = ?
        ORDER BY created_at ASC
        """,
        (
            session_id,
            condition.study.value,
            condition.environment.value,
            condition.feedback_timing.value,
            condition.modality.value,
            int(practice),
        ),
    ).fetchall()


def _next_condition_code(
    db: Database,
    *,
    session_id: str,
    study: Study,
    practice: bool,
) -> str:
    prefix = "TR" if practice else "T"
    rows = db.experimental_conn.execute(
        """
        SELECT DISTINCT condition_code
        FROM trials
        WHERE session_id = ? AND study = ? AND practice = ?
        """,
        (session_id, study.value, int(practice)),
    ).fetchall()
    max_n = 0
    for row in rows:
        code = row["condition_code"]
        if not code:
            continue
        match = _CONDITION_CODE_RE.match(code)
        if match and code.startswith(prefix):
            max_n = max(max_n, int(match.group(1)))
    return f"{prefix}{max_n + 1:02d}"


def _next_run_code(rows) -> str:
    max_n = 0
    for row in rows:
        code = row["run_code"]
        if not code:
            continue
        match = _RUN_CODE_RE.match(code)
        if match:
            max_n = max(max_n, int(match.group(1)))
    return f"R{max_n + 1:02d}"


def allocate_trial_storage_identity(
    db: Database,
    *,
    session_id: str,
    condition: ExperimentCondition,
    practice: bool,
) -> TrialStorageIdentity:
    """Allocate/reuse T## and allocate the next R## for one condition."""
    rows = _condition_rows(
        db,
        session_id=session_id,
        condition=condition,
        practice=practice,
    )

    condition_code = next(
        (row["condition_code"] for row in rows if row["condition_code"]),
        None,
    )
    if condition_code is None:
        condition_code = _next_condition_code(
            db,
            session_id=session_id,
            study=condition.study,
            practice=practice,
        )

    run_code = _next_run_code(rows)
    name = condition_name(condition)

    study_code = {Study.STUDY_1: "ST1", Study.STUDY_2: "ST2", Study.OBSERVATION: "OBS"}[condition.study]
    if condition.study == Study.OBSERVATION:
        # Observation is already a phase name; avoid the awkward "OBSST"
        # identifier used by the generic Study-1/Study-2 phase suffix.
        scope_code = "OBSTR" if practice else "OBS"
    else:
        scope_code = f"{study_code}{'TR' if practice else 'ST'}"
    trial_id = f"{session_id}_{scope_code}_{condition_code}_{run_code}"

    relative_dir = (
        study_relative_root(condition.study, practice)
        / f"{condition_code}_{name}"
        / run_code
    )

    return TrialStorageIdentity(
        condition_code=condition_code,
        run_code=run_code,
        condition_name=name,
        trial_id=trial_id,
        relative_dir=relative_dir,
    )


def preview_trial_storage_identity(
    db: Database,
    *,
    session_id: str,
    condition: ExperimentCondition,
    practice: bool,
) -> TrialStorageIdentity:
    """Read-only alias used by the GUI for the next folder preview."""
    return allocate_trial_storage_identity(
        db,
        session_id=session_id,
        condition=condition,
        practice=practice,
    )
