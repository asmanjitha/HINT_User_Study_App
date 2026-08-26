"""Workflow Manager.

Tracks a participant's progress through the fixed study sequence:

    Registration -> Study 1 Training -> Study 1 Study
                  -> Study 2 Training -> Study 2 Study

The experimental Study panels are protocol-aware:

* Study 1 Training keeps the existing 2 x 4 familiarization matrix.
* Study 1 Study follows the IRB-facing three-setting flow.  The Gridworld
  setting has two required timing conditions (Requested and Anytime), the
  indoor-room setting is one required explicit-feedback task, and the
  experimenter-navigation baseline is one required task.
* Study 2 Study focuses the condition tracker on multimodal feedback in the
  2D Gridworld.  Each required modality must be completed at least once.

All workflow runs for one participant reuse the same active collection Session
(S01) until that session is closed. Each exact experimental condition gets a
stable T##/TR## code and repeated collections become R01, R02, ... .
"""

from __future__ import annotations

import logging
import time
from typing import NamedTuple, Optional

from core.database import Database
from core.event_bus import EventBus
from core.id_generator import generate_run_id
from core.session_manager import SessionManager
from models.enums import (
    CollectionRunStatus,
    Environment,
    FeedbackTiming,
    Modality,
    StepOverallStatus,
    StepRunStatus,
    Study,
    TrialStatus,
    WorkflowStep,
)
from models.session import Session
from models.workflow import StepRun

logger = logging.getLogger(__name__)

STEP_ORDER: list[WorkflowStep] = [
    WorkflowStep.REGISTRATION,
    WorkflowStep.STUDY1_TRAINING,
    WorkflowStep.STUDY1_STUDY,
    WorkflowStep.STUDY2_TRAINING,
    WorkflowStep.STUDY2_STUDY,
]

STEP_STUDY: dict[WorkflowStep, Optional[Study]] = {
    WorkflowStep.REGISTRATION: None,
    WorkflowStep.STUDY1_TRAINING: Study.STUDY_1,
    WorkflowStep.STUDY1_STUDY: Study.STUDY_1,
    WorkflowStep.STUDY2_TRAINING: Study.STUDY_2,
    WorkflowStep.STUDY2_STUDY: Study.STUDY_2,
}

STEP_PRACTICE: dict[WorkflowStep, bool] = {
    WorkflowStep.REGISTRATION: False,
    WorkflowStep.STUDY1_TRAINING: True,
    WorkflowStep.STUDY1_STUDY: False,
    WorkflowStep.STUDY2_TRAINING: True,
    WorkflowStep.STUDY2_STUDY: False,
}

STEP_LABELS: dict[WorkflowStep, str] = {
    WorkflowStep.REGISTRATION: "Registration",
    WorkflowStep.STUDY1_TRAINING: "Study 1 — Training",
    WorkflowStep.STUDY1_STUDY: "Study 1 — Study",
    WorkflowStep.STUDY2_TRAINING: "Study 2 — Training",
    WorkflowStep.STUDY2_STUDY: "Study 2 — Study",
}

REPEATABLE_STEPS = {
    WorkflowStep.STUDY1_TRAINING,
    WorkflowStep.STUDY1_STUDY,
    WorkflowStep.STUDY2_TRAINING,
    WorkflowStep.STUDY2_STUDY,
}

# ---------------------------------------------------------------------------
# Study 1 Training: unchanged familiarization matrix (2 timings x 4 modes)
# ---------------------------------------------------------------------------
STUDY1_TRAINING_REQUIRED_TIMINGS: tuple[FeedbackTiming, ...] = (
    FeedbackTiming.REQUESTED,
    FeedbackTiming.ANYTIME,
)
STUDY1_TRAINING_REQUIRED_MODALITIES: tuple[Modality, ...] = (
    Modality.KEYBOARD,
    Modality.JOYSTICK,
    Modality.VOICE,
    Modality.EYE_GAZE,
)
STUDY1_TRAINING_REQUIRED_CONDITION_COUNT = (
    len(STUDY1_TRAINING_REQUIRED_TIMINGS)
    * len(STUDY1_TRAINING_REQUIRED_MODALITIES)
)

# Backward-compatible aliases.  Older tests/modules imported these names for
# the training matrix.  Experimental Study 1 no longer uses this 2 x 4 matrix.
STUDY1_REQUIRED_TIMINGS = STUDY1_TRAINING_REQUIRED_TIMINGS
STUDY1_REQUIRED_MODALITIES = STUDY1_TRAINING_REQUIRED_MODALITIES
STUDY1_REQUIRED_CONDITION_COUNT = STUDY1_TRAINING_REQUIRED_CONDITION_COUNT

EXPLICIT_STUDY1_MODALITIES: tuple[Modality, ...] = (
    Modality.KEYBOARD,
    Modality.JOYSTICK,
)


class Study1ProtocolCondition(NamedTuple):
    key: str
    label: str
    environment: Environment
    feedback_timing: Optional[FeedbackTiming]
    allowed_modalities: tuple[Modality, ...]


STUDY1_STUDY_REQUIRED_CONDITIONS: tuple[Study1ProtocolCondition, ...] = (
    Study1ProtocolCondition(
        "grid_requested",
        "1A. 2D Gridworld — System-requested feedback",
        Environment.GRIDWORLD,
        FeedbackTiming.REQUESTED,
        EXPLICIT_STUDY1_MODALITIES,
    ),
    Study1ProtocolCondition(
        "grid_anytime",
        "1B. 2D Gridworld — Anytime feedback",
        Environment.GRIDWORLD,
        FeedbackTiming.ANYTIME,
        EXPLICIT_STUDY1_MODALITIES,
    ),
    Study1ProtocolCondition(
        "room_navigation",
        "2. Indoor room navigation — Explicit feedback",
        Environment.CONTINUOUS_ROOM,
        None,  # timing is recorded, but either Requested/Anytime can satisfy this sub-step
        EXPLICIT_STUDY1_MODALITIES,
    ),
    Study1ProtocolCondition(
        "baseline_navigation",
        "3. Baseline — Experimenter virtual navigation",
        Environment.HUMAN_AGENT_BASELINE,
        FeedbackTiming.NOT_APPLICABLE,
        (Modality.NONE,),
    ),
)
STUDY1_STUDY_REQUIRED_CONDITION_COUNT = len(STUDY1_STUDY_REQUIRED_CONDITIONS)

# ---------------------------------------------------------------------------
# Study 2 Study: multimodal feedback focus in 2D Gridworld
# ---------------------------------------------------------------------------
STUDY2_REQUIRED_MODALITIES: tuple[Modality, ...] = (
    Modality.KEYBOARD,
    Modality.JOYSTICK,
    Modality.VOICE,
    Modality.IMPLICIT,
)
STUDY2_REQUIRED_CONDITION_COUNT = len(STUDY2_REQUIRED_MODALITIES)

_ACTIVE_TRIAL_STATUSES = {
    TrialStatus.CREATED.value,
    TrialStatus.RUNNING.value,
    TrialStatus.PRACTICE.value,
    TrialStatus.PAUSED.value,
}


class Study1TrainingConditionSummary(NamedTuple):
    feedback_timing: FeedbackTiming
    modality: Modality
    status: str
    completed_trials: int
    total_trials: int
    active_trial_id: Optional[str]
    last_trial_id: Optional[str]


# Compatibility alias used by the existing training panel/tests.
Study1ConditionSummary = Study1TrainingConditionSummary


class Study1StudyConditionSummary(NamedTuple):
    key: str
    label: str
    environment: Environment
    feedback_timing: Optional[FeedbackTiming]
    status: str
    completed_trials: int
    total_trials: int
    active_trial_id: Optional[str]
    last_trial_id: Optional[str]
    last_modality: Optional[Modality]
    last_feedback_timing: Optional[FeedbackTiming]


class Study2ConditionSummary(NamedTuple):
    modality: Modality
    status: str
    completed_trials: int
    total_trials: int
    active_trial_id: Optional[str]
    last_trial_id: Optional[str]
    last_feedback_timing: Optional[FeedbackTiming]


class StepSummary(NamedTuple):
    step: WorkflowStep
    overall_status: StepOverallStatus
    completed_count: int
    total_runs: int
    active_run: Optional[StepRun]
    last_run: Optional[StepRun]


class WorkflowManager:
    def __init__(self, db: Database, session_manager: SessionManager, event_bus: EventBus) -> None:
        self._db = db
        self._session_manager = session_manager
        self._event_bus = event_bus

    # -- Starting / ending runs ---------------------------------------------
    def start_run(self, participant_code: str, step: WorkflowStep) -> tuple[StepRun, Session]:
        if step == WorkflowStep.REGISTRATION:
            raise ValueError("Registration is not a repeatable run; create the participant instead.")

        study = STEP_STUDY[step]
        assert study is not None

        session = self._session_manager.get_or_create_active_session(participant_code)

        run_id = generate_run_id(self._db, participant_code, step)
        run = StepRun(
            run_id=run_id,
            participant_code=participant_code,
            step=step,
            study=study,
            practice=STEP_PRACTICE[step],
            status=StepRunStatus.IN_PROGRESS,
            session_id=session.session_id,
            started_at=time.time(),
        )
        self._insert(run)
        logger.info("Started run %s (%s) for %s", run.run_id, step.value, participant_code)
        return run, session

    def attach_trial(self, run_id: str, trial_id: str) -> None:
        self._db.experimental_conn.execute(
            "UPDATE workflow_runs SET trial_id = ? WHERE run_id = ?", (trial_id, run_id)
        )
        self._db.experimental_conn.commit()

    def end_run(
        self,
        run_id: str,
        completed: bool = True,
        notes: str = "",
        *,
        outcome: CollectionRunStatus | None = None,
    ) -> Optional[StepRun]:
        """Finish one GUI attempt without closing the participant S## session."""
        run = self.get_run(run_id)
        if run is None:
            return None

        if outcome == CollectionRunStatus.INVALID:
            run.status = StepRunStatus.INVALID
        elif outcome == CollectionRunStatus.ABORTED or not completed:
            run.status = StepRunStatus.ABORTED
        else:
            run.status = StepRunStatus.COMPLETED
        run.ended_at = time.time()
        if notes:
            run.notes = notes

        self._persist(run)

        logger.info("Ended run %s -> %s", run.run_id, run.status.value)
        return run

    # -- Queries -------------------------------------------------------------
    def get_run(self, run_id: str) -> Optional[StepRun]:
        row = self._db.experimental_conn.execute(
            "SELECT * FROM workflow_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        return None if row is None else self._row_to_run(row)

    def list_runs(self, participant_code: str, step: Optional[WorkflowStep] = None) -> list[StepRun]:
        if step is not None:
            rows = self._db.experimental_conn.execute(
                "SELECT * FROM workflow_runs WHERE participant_code = ? AND step = ? "
                "ORDER BY created_at ASC",
                (participant_code, step.value),
            ).fetchall()
        else:
            rows = self._db.experimental_conn.execute(
                "SELECT * FROM workflow_runs WHERE participant_code = ? ORDER BY created_at ASC",
                (participant_code,),
            ).fetchall()
        return [self._row_to_run(row) for row in rows]

    def step_status(
        self, participant_code: str, step: WorkflowStep, *, participant_exists: bool = True
    ) -> StepSummary:
        if step == WorkflowStep.REGISTRATION:
            status = StepOverallStatus.COMPLETED if participant_exists else StepOverallStatus.NOT_STARTED
            return StepSummary(
                step,
                status,
                1 if participant_exists else 0,
                1 if participant_exists else 0,
                None,
                None,
            )

        runs = self.list_runs(participant_code, step)
        if not runs:
            # There can be persisted trials from an older build without a
            # workflow-run row.  Keep the menu conservative and call the step
            # Not Started until the new workflow has a run.
            return StepSummary(step, StepOverallStatus.NOT_STARTED, 0, 0, None, None)

        active = next((r for r in runs if r.status == StepRunStatus.IN_PROGRESS), None)

        if step == WorkflowStep.STUDY1_TRAINING:
            completed_count = sum(
                1
                for item in self.study1_condition_statuses(participant_code, practice=True)
                if item.status == "Completed"
            )
            required_count = STUDY1_TRAINING_REQUIRED_CONDITION_COUNT
        elif step == WorkflowStep.STUDY1_STUDY:
            completed_count = sum(
                1
                for item in self.study1_study_condition_statuses(participant_code)
                if item.status == "Completed"
            )
            required_count = STUDY1_STUDY_REQUIRED_CONDITION_COUNT
        elif step == WorkflowStep.STUDY2_STUDY:
            completed_count = sum(
                1
                for item in self.study2_condition_statuses(participant_code)
                if item.status == "Completed"
            )
            required_count = STUDY2_REQUIRED_CONDITION_COUNT
        else:
            completed_count = sum(1 for r in runs if r.status == StepRunStatus.COMPLETED)
            required_count = 1

        if active is not None:
            overall = StepOverallStatus.IN_PROGRESS
        elif step in (
            WorkflowStep.STUDY1_TRAINING,
            WorkflowStep.STUDY1_STUDY,
            WorkflowStep.STUDY2_STUDY,
        ):
            overall = (
                StepOverallStatus.COMPLETED
                if completed_count == required_count
                else StepOverallStatus.IN_PROGRESS
            )
        elif completed_count > 0:
            overall = StepOverallStatus.COMPLETED
        else:
            overall = StepOverallStatus.NOT_STARTED

        return StepSummary(step, overall, completed_count, len(runs), active, runs[-1])

    def all_step_statuses(
        self, participant_code: str, *, participant_exists: bool = True
    ) -> list[StepSummary]:
        return [
            self.step_status(participant_code, step, participant_exists=participant_exists)
            for step in STEP_ORDER
        ]

    # -- Study 1 Training matrix --------------------------------------------
    def study1_condition_statuses(
        self, participant_code: str, *, practice: bool = False
    ) -> list[Study1TrainingConditionSummary]:
        """Status for the legacy 2 x 4 Study 1 matrix.

        The revised GUI uses this only for Study 1 Training (practice=True).
        ``practice=False`` is retained for compatibility with old data/tests,
        but experimental Study 1 completion is now determined by
        :meth:`study1_study_condition_statuses`.
        """
        rows = self._db.experimental_conn.execute(
            """
            SELECT trial_id, feedback_timing, modality, status, collection_status, created_at
            FROM trials
            WHERE participant_code = ?
              AND study = ?
              AND environment = ?
              AND practice = ?
            ORDER BY created_at ASC
            """,
            (
                participant_code,
                Study.STUDY_1.value,
                Environment.GRIDWORLD.value,
                int(practice),
            ),
        ).fetchall()

        summaries: list[Study1TrainingConditionSummary] = []
        for timing in STUDY1_TRAINING_REQUIRED_TIMINGS:
            for modality in STUDY1_TRAINING_REQUIRED_MODALITIES:
                matching = [
                    row
                    for row in rows
                    if row["feedback_timing"] == timing.value
                    and row["modality"] == modality.value
                ]
                completed = [
                    row for row in matching if self._row_is_valid_completion(row)
                ]
                active = next(
                    (row for row in reversed(matching) if row["status"] in _ACTIVE_TRIAL_STATUSES),
                    None,
                )
                status = self._condition_status_label(matching, completed, active)
                summaries.append(
                    Study1TrainingConditionSummary(
                        feedback_timing=timing,
                        modality=modality,
                        status=status,
                        completed_trials=len(completed),
                        total_trials=len(matching),
                        active_trial_id=active["trial_id"] if active is not None else None,
                        last_trial_id=matching[-1]["trial_id"] if matching else None,
                    )
                )
        return summaries

    def study1_condition_status(
        self,
        participant_code: str,
        feedback_timing: FeedbackTiming,
        modality: Modality,
        *,
        practice: bool = False,
    ) -> Study1TrainingConditionSummary:
        for item in self.study1_condition_statuses(participant_code, practice=practice):
            if item.feedback_timing == feedback_timing and item.modality == modality:
                return item
        raise ValueError("Condition is not part of the Study 1 training matrix")

    def next_incomplete_study1_condition(
        self, participant_code: str, *, practice: bool = False
    ) -> Optional[Study1TrainingConditionSummary]:
        for item in self.study1_condition_statuses(participant_code, practice=practice):
            if item.status != "Completed":
                return item
        return None

    # -- Study 1 experimental protocol -------------------------------------
    def study1_study_condition_statuses(
        self, participant_code: str
    ) -> list[Study1StudyConditionSummary]:
        rows = self._db.experimental_conn.execute(
            """
            SELECT trial_id, environment, feedback_timing, modality, status, collection_status, created_at
            FROM trials
            WHERE participant_code = ?
              AND study = ?
              AND practice = 0
            ORDER BY created_at ASC
            """,
            (participant_code, Study.STUDY_1.value),
        ).fetchall()

        summaries: list[Study1StudyConditionSummary] = []
        for required in STUDY1_STUDY_REQUIRED_CONDITIONS:
            matching = [row for row in rows if self._matches_study1_required(row, required)]
            completed = [
                row for row in matching if self._row_is_valid_completion(row)
            ]
            active = next(
                (row for row in reversed(matching) if row["status"] in _ACTIVE_TRIAL_STATUSES),
                None,
            )
            last = matching[-1] if matching else None
            summaries.append(
                Study1StudyConditionSummary(
                    key=required.key,
                    label=required.label,
                    environment=required.environment,
                    feedback_timing=required.feedback_timing,
                    status=self._condition_status_label(matching, completed, active),
                    completed_trials=len(completed),
                    total_trials=len(matching),
                    active_trial_id=active["trial_id"] if active is not None else None,
                    last_trial_id=last["trial_id"] if last is not None else None,
                    last_modality=(Modality(last["modality"]) if last is not None else None),
                    last_feedback_timing=(
                        FeedbackTiming(last["feedback_timing"])
                        if last is not None and last["feedback_timing"]
                        else None
                    ),
                )
            )
        return summaries

    def study1_study_condition_status(
        self, participant_code: str, key: str
    ) -> Study1StudyConditionSummary:
        for item in self.study1_study_condition_statuses(participant_code):
            if item.key == key:
                return item
        raise ValueError(f"Unknown Study 1 protocol condition: {key}")

    def next_incomplete_study1_study_condition(
        self, participant_code: str
    ) -> Optional[Study1StudyConditionSummary]:
        for item in self.study1_study_condition_statuses(participant_code):
            if item.status != "Completed":
                return item
        return None

    @staticmethod
    def _matches_study1_required(row, required: Study1ProtocolCondition) -> bool:
        if row["environment"] != required.environment.value:
            return False
        if required.feedback_timing is not None and row["feedback_timing"] != required.feedback_timing.value:
            return False
        return row["modality"] in {m.value for m in required.allowed_modalities}

    # -- Study 2 multimodal Gridworld --------------------------------------
    def study2_condition_statuses(self, participant_code: str) -> list[Study2ConditionSummary]:
        rows = self._db.experimental_conn.execute(
            """
            SELECT trial_id, feedback_timing, modality, status, collection_status, created_at
            FROM trials
            WHERE participant_code = ?
              AND study = ?
              AND environment = ?
              AND practice = 0
            ORDER BY created_at ASC
            """,
            (
                participant_code,
                Study.STUDY_2.value,
                Environment.GRIDWORLD.value,
            ),
        ).fetchall()

        summaries: list[Study2ConditionSummary] = []
        for modality in STUDY2_REQUIRED_MODALITIES:
            matching = [row for row in rows if row["modality"] == modality.value]
            completed = [
                row for row in matching if self._row_is_valid_completion(row)
            ]
            active = next(
                (row for row in reversed(matching) if row["status"] in _ACTIVE_TRIAL_STATUSES),
                None,
            )
            last = matching[-1] if matching else None
            summaries.append(
                Study2ConditionSummary(
                    modality=modality,
                    status=self._condition_status_label(matching, completed, active),
                    completed_trials=len(completed),
                    total_trials=len(matching),
                    active_trial_id=active["trial_id"] if active is not None else None,
                    last_trial_id=last["trial_id"] if last is not None else None,
                    last_feedback_timing=(
                        FeedbackTiming(last["feedback_timing"])
                        if last is not None and last["feedback_timing"]
                        else None
                    ),
                )
            )
        return summaries

    def study2_condition_status(
        self, participant_code: str, modality: Modality
    ) -> Study2ConditionSummary:
        for item in self.study2_condition_statuses(participant_code):
            if item.modality == modality:
                return item
        raise ValueError("Modality is not part of the required Study 2 Gridworld set")

    def next_incomplete_study2_condition(
        self, participant_code: str
    ) -> Optional[Study2ConditionSummary]:
        for item in self.study2_condition_statuses(participant_code):
            if item.status != "Completed":
                return item
        return None

    @staticmethod
    def _row_is_valid_completion(row) -> bool:
        """Only VALID R## attempts satisfy a study condition.

        Legacy v0.8 rows have no meaningful collection_status and are treated
        as valid when their lifecycle status is Completed.
        """
        if row["status"] != TrialStatus.COMPLETED.value:
            return False
        raw = row["collection_status"] if "collection_status" in row.keys() else None
        return raw in (None, "", CollectionRunStatus.PENDING.value, CollectionRunStatus.VALID.value)

    @staticmethod
    def _condition_status_label(matching, completed, active) -> str:
        if completed:
            return "Completed"
        if active is not None:
            return "In Progress"
        if matching:
            return "Needs Repeat"
        return "Not Started"

    def has_active_run(self, participant_code: str) -> Optional[StepRun]:
        for step in STEP_ORDER:
            if step == WorkflowStep.REGISTRATION:
                continue
            summary = self.step_status(participant_code, step)
            if summary.active_run is not None:
                return summary.active_run
        return None

    # -- Persistence --------------------------------------------------------
    def _insert(self, run: StepRun) -> None:
        self._db.experimental_conn.execute(
            """
            INSERT INTO workflow_runs
            (run_id, participant_code, step, study, practice, status,
             session_id, trial_id, created_at, started_at, ended_at, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run.run_id,
                run.participant_code,
                run.step.value,
                run.study.value if run.study else None,
                int(run.practice),
                run.status.value,
                run.session_id,
                run.trial_id,
                run.created_at,
                run.started_at,
                run.ended_at,
                run.notes,
            ),
        )
        self._db.experimental_conn.commit()

    def _persist(self, run: StepRun) -> None:
        self._db.experimental_conn.execute(
            "UPDATE workflow_runs SET status = ?, ended_at = ?, notes = ? WHERE run_id = ?",
            (run.status.value, run.ended_at, run.notes, run.run_id),
        )
        self._db.experimental_conn.commit()

    @staticmethod
    def _row_to_run(row) -> StepRun:
        return StepRun(
            run_id=row["run_id"],
            participant_code=row["participant_code"],
            step=WorkflowStep(row["step"]),
            study=Study(row["study"]) if row["study"] else None,
            practice=bool(row["practice"]),
            status=StepRunStatus(row["status"]),
            session_id=row["session_id"],
            trial_id=row["trial_id"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            notes=row["notes"] or "",
        )
