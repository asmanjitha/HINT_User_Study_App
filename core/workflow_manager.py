"""Participant workflow and protocol completion tracking.

Current protocol:

Registration -> shared Training Phase -> Study 1 (when to intervene)
             -> Study 2 (how to provide feedback) -> Agent Observation Phase

Study 2 is intentionally experimenter-finished: participants may complete only
one or two selected modalities.  The final observation phase contains no human
feedback and requires both Gridworld and continuous-room agent learning runs.
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
    WorkflowStep.STUDY2_STUDY,
    WorkflowStep.AGENT_OBSERVATION,
]

STEP_STUDY: dict[WorkflowStep, Optional[Study]] = {
    WorkflowStep.REGISTRATION: None,
    WorkflowStep.STUDY1_TRAINING: Study.STUDY_1,
    WorkflowStep.STUDY1_STUDY: Study.STUDY_1,
    WorkflowStep.STUDY2_TRAINING: Study.STUDY_2,  # legacy hidden step
    WorkflowStep.STUDY2_STUDY: Study.STUDY_2,
    WorkflowStep.AGENT_OBSERVATION: Study.OBSERVATION,
}

STEP_PRACTICE: dict[WorkflowStep, bool] = {
    WorkflowStep.REGISTRATION: False,
    WorkflowStep.STUDY1_TRAINING: True,
    WorkflowStep.STUDY1_STUDY: False,
    WorkflowStep.STUDY2_TRAINING: True,
    WorkflowStep.STUDY2_STUDY: False,
    WorkflowStep.AGENT_OBSERVATION: False,
}

STEP_LABELS: dict[WorkflowStep, str] = {
    WorkflowStep.REGISTRATION: "Registration",
    WorkflowStep.STUDY1_TRAINING: "Training Phase",
    WorkflowStep.STUDY1_STUDY: "Study 1 — When Should a Human Intervene?",
    WorkflowStep.STUDY2_TRAINING: "Study 2 — Training (Legacy)",
    WorkflowStep.STUDY2_STUDY: "Study 2 — How Should a Human Provide Feedback?",
    WorkflowStep.AGENT_OBSERVATION: "Agent Observation Phase — No Human Feedback",
}

REPEATABLE_STEPS = set(STEP_STUDY) - {WorkflowStep.REGISTRATION}


class TrainingCondition(NamedTuple):
    key: str
    label: str
    study: Study
    environment: Environment
    feedback_timing: FeedbackTiming
    modality: Modality
    required: bool = True


# Anytime practice is deliberately Keyboard-only.  HoloLens familiarization is
# optional and last; it never blocks the experimental studies.
TRAINING_CONDITIONS: tuple[TrainingCondition, ...] = (
    TrainingCondition(
        "grid_requested_keyboard",
        "Gridworld — System-requested feedback — Keyboard",
        Study.STUDY_1, Environment.GRIDWORLD, FeedbackTiming.REQUESTED, Modality.KEYBOARD,
    ),
    TrainingCondition(
        "grid_anytime_keyboard",
        "Gridworld — Anytime feedback — Keyboard",
        Study.STUDY_1, Environment.GRIDWORLD, FeedbackTiming.ANYTIME, Modality.KEYBOARD,
    ),
    TrainingCondition(
        "room_requested_keyboard",
        "Continuous action space — System-requested feedback — Keyboard",
        Study.STUDY_1, Environment.CONTINUOUS_ROOM, FeedbackTiming.REQUESTED, Modality.KEYBOARD,
    ),
    TrainingCondition(
        "room_anytime_keyboard",
        "Continuous action space — Anytime feedback — Keyboard",
        Study.STUDY_1, Environment.CONTINUOUS_ROOM, FeedbackTiming.ANYTIME, Modality.KEYBOARD,
    ),
    TrainingCondition(
        "grid_requested_joystick",
        "Gridworld — System-requested feedback — Joystick",
        Study.STUDY_2, Environment.GRIDWORLD, FeedbackTiming.REQUESTED, Modality.JOYSTICK,
    ),
    TrainingCondition(
        "grid_requested_voice",
        "Gridworld — System-requested feedback — Voice",
        Study.STUDY_2, Environment.GRIDWORLD, FeedbackTiming.REQUESTED, Modality.VOICE,
    ),
    TrainingCondition(
        "hololens_optional",
        "Optional HoloLens familiarization / sensor check (no feedback)",
        Study.OBSERVATION, Environment.GRIDWORLD, FeedbackTiming.NOT_APPLICABLE, Modality.NONE,
        False,
    ),
)
TRAINING_REQUIRED_CONDITIONS = tuple(c for c in TRAINING_CONDITIONS if c.required)
STUDY1_TRAINING_REQUIRED_CONDITION_COUNT = len(TRAINING_REQUIRED_CONDITIONS)
# Backward-compatible count name used by older GUI/tests.
STUDY1_REQUIRED_CONDITION_COUNT = STUDY1_TRAINING_REQUIRED_CONDITION_COUNT
STUDY1_REQUIRED_TIMINGS = (FeedbackTiming.REQUESTED, FeedbackTiming.ANYTIME)
STUDY1_REQUIRED_MODALITIES = (Modality.KEYBOARD,)

EXPLICIT_STUDY1_MODALITIES: tuple[Modality, ...] = (Modality.KEYBOARD,)


class Study1ProtocolCondition(NamedTuple):
    key: str
    label: str
    environment: Environment
    feedback_timing: FeedbackTiming
    allowed_modalities: tuple[Modality, ...]


STUDY1_STUDY_REQUIRED_CONDITIONS: tuple[Study1ProtocolCondition, ...] = (
    Study1ProtocolCondition(
        "grid_requested", "1A. Gridworld — System-requested feedback",
        Environment.GRIDWORLD, FeedbackTiming.REQUESTED, (Modality.KEYBOARD,),
    ),
    Study1ProtocolCondition(
        "grid_anytime", "1B. Gridworld — Anytime feedback",
        Environment.GRIDWORLD, FeedbackTiming.ANYTIME, (Modality.KEYBOARD,),
    ),
    Study1ProtocolCondition(
        "room_requested", "1C. Continuous action space — System-requested feedback",
        Environment.CONTINUOUS_ROOM, FeedbackTiming.REQUESTED, (Modality.KEYBOARD,),
    ),
    Study1ProtocolCondition(
        "room_anytime", "1D. Continuous action space — Anytime feedback",
        Environment.CONTINUOUS_ROOM, FeedbackTiming.ANYTIME, (Modality.KEYBOARD,),
    ),
)
STUDY1_STUDY_REQUIRED_CONDITION_COUNT = len(STUDY1_STUDY_REQUIRED_CONDITIONS)

# Study 2 uses only these modalities. Completion is NOT tied to all three.
STUDY2_REQUIRED_MODALITIES: tuple[Modality, ...] = (
    Modality.KEYBOARD,
    Modality.JOYSTICK,
    Modality.VOICE,
)
STUDY2_REQUIRED_CONDITION_COUNT = len(STUDY2_REQUIRED_MODALITIES)
STUDY2_FINISHED_NOTE = "STUDY2_FINISHED_BY_EXPERIMENTER"


class ObservationCondition(NamedTuple):
    key: str
    label: str
    environment: Environment


OBSERVATION_REQUIRED_CONDITIONS: tuple[ObservationCondition, ...] = (
    ObservationCondition("grid_observation", "Gridworld — Agent learns without human feedback", Environment.GRIDWORLD),
    ObservationCondition("room_observation", "Continuous action space — Agent learns without human feedback", Environment.CONTINUOUS_ROOM),
)
OBSERVATION_REQUIRED_CONDITION_COUNT = len(OBSERVATION_REQUIRED_CONDITIONS)

_ACTIVE_TRIAL_STATUSES = {
    TrialStatus.CREATED.value,
    TrialStatus.RUNNING.value,
    TrialStatus.PRACTICE.value,
    TrialStatus.PAUSED.value,
}
STUDY1_TRAINING_QUICK_PASS_NOTE = "QUICK_PASS_ALL_REQUIRED_TRAINING_TESTS"


class TrainingConditionSummary(NamedTuple):
    key: str
    label: str
    study: Study
    environment: Environment
    feedback_timing: FeedbackTiming
    modality: Modality
    required: bool
    status: str
    completed_trials: int
    total_trials: int
    active_trial_id: Optional[str]
    last_trial_id: Optional[str]


# Compatibility shape for old callers.
class Study1TrainingConditionSummary(NamedTuple):
    feedback_timing: FeedbackTiming
    modality: Modality
    status: str
    completed_trials: int
    total_trials: int
    active_trial_id: Optional[str]
    last_trial_id: Optional[str]

Study1ConditionSummary = Study1TrainingConditionSummary


class Study1StudyConditionSummary(NamedTuple):
    key: str
    label: str
    environment: Environment
    feedback_timing: FeedbackTiming
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


class ObservationConditionSummary(NamedTuple):
    key: str
    label: str
    environment: Environment
    status: str
    completed_trials: int
    total_trials: int
    active_trial_id: Optional[str]
    last_trial_id: Optional[str]


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

    def start_run(self, participant_code: str, step: WorkflowStep) -> tuple[StepRun, Session]:
        if step == WorkflowStep.REGISTRATION:
            raise ValueError("Registration is not a repeatable run; create the participant instead.")
        study = STEP_STUDY[step]
        assert study is not None
        session = self._session_manager.get_or_create_active_session(participant_code)
        run = StepRun(
            run_id=generate_run_id(self._db, participant_code, step),
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

    def end_run(self, run_id: str, completed: bool = True, notes: str = "", *, outcome: CollectionRunStatus | None = None) -> Optional[StepRun]:
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
        return run

    # ---- Training ------------------------------------------------------
    def study1_training_quick_passed(self, participant_code: str) -> bool:
        row = self._db.experimental_conn.execute(
            """SELECT 1 FROM workflow_runs WHERE participant_code=? AND step=? AND status=? AND notes=? LIMIT 1""",
            (participant_code, WorkflowStep.STUDY1_TRAINING.value, StepRunStatus.COMPLETED.value, STUDY1_TRAINING_QUICK_PASS_NOTE),
        ).fetchone()
        return row is not None

    def quick_pass_study1_training(self, participant_code: str) -> StepRun:
        if self.study1_training_quick_passed(participant_code):
            for run in reversed(self.list_runs(participant_code, WorkflowStep.STUDY1_TRAINING)):
                if run.notes == STUDY1_TRAINING_QUICK_PASS_NOTE and run.status == StepRunStatus.COMPLETED:
                    return run
        blocking = self.has_active_run(participant_code)
        if blocking is not None:
            raise ValueError(f"Cannot Quick Pass training while run {blocking.run_id} is in progress.")
        actual = sum(1 for s in self.training_condition_statuses(participant_code) if s.required and s.status == "Completed")
        if actual == STUDY1_TRAINING_REQUIRED_CONDITION_COUNT:
            raise ValueError("Training Phase is already complete.")
        run, _ = self.start_run(participant_code, WorkflowStep.STUDY1_TRAINING)
        done = self.end_run(run.run_id, completed=True, notes=STUDY1_TRAINING_QUICK_PASS_NOTE)
        assert done is not None
        return done

    def training_condition_statuses(self, participant_code: str) -> list[TrainingConditionSummary]:
        quick = self.study1_training_quick_passed(participant_code)
        rows = self._db.experimental_conn.execute(
            """SELECT trial_id, study, environment, feedback_timing, modality, status, collection_status, created_at
               FROM trials WHERE participant_code=? AND practice=1 ORDER BY created_at ASC""",
            (participant_code,),
        ).fetchall()
        out: list[TrainingConditionSummary] = []
        for req in TRAINING_CONDITIONS:
            matching = [r for r in rows if self._matches_training(r, req)]
            completed = [r for r in matching if self._row_is_valid_completion(r)]
            active = next((r for r in reversed(matching) if r["status"] in _ACTIVE_TRIAL_STATUSES), None)
            status = "Completed" if (quick and req.required) else self._condition_status_label(matching, completed, active)
            out.append(TrainingConditionSummary(
                req.key, req.label, req.study, req.environment, req.feedback_timing, req.modality, req.required,
                status, len(completed), len(matching), active["trial_id"] if active else None,
                matching[-1]["trial_id"] if matching else None,
            ))
        return out

    def training_condition_status(self, participant_code: str, key: str) -> TrainingConditionSummary:
        for item in self.training_condition_statuses(participant_code):
            if item.key == key:
                return item
        raise ValueError(f"Unknown training condition: {key}")

    def next_incomplete_training_condition(self, participant_code: str) -> Optional[TrainingConditionSummary]:
        for item in self.training_condition_statuses(participant_code):
            if item.required and item.status != "Completed":
                return item
        return None

    @staticmethod
    def _matches_training(row, req: TrainingCondition) -> bool:
        return (
            row["study"] == req.study.value and row["environment"] == req.environment.value
            and row["feedback_timing"] == req.feedback_timing.value and row["modality"] == req.modality.value
        )

    # Compatibility methods retained for code/data from the earlier matrix.
    def study1_condition_statuses(self, participant_code: str, *, practice: bool = False) -> list[Study1TrainingConditionSummary]:
        rows = self._db.experimental_conn.execute(
            """SELECT trial_id, feedback_timing, modality, status, collection_status, created_at FROM trials
               WHERE participant_code=? AND study=? AND environment=? AND practice=? ORDER BY created_at ASC""",
            (participant_code, Study.STUDY_1.value, Environment.GRIDWORLD.value, int(practice)),
        ).fetchall()
        pairs = ((FeedbackTiming.REQUESTED, Modality.KEYBOARD), (FeedbackTiming.ANYTIME, Modality.KEYBOARD))
        quick = practice and self.study1_training_quick_passed(participant_code)
        out=[]
        for timing, modality in pairs:
            matching=[r for r in rows if r["feedback_timing"]==timing.value and r["modality"]==modality.value]
            completed=[r for r in matching if self._row_is_valid_completion(r)]
            active=next((r for r in reversed(matching) if r["status"] in _ACTIVE_TRIAL_STATUSES),None)
            out.append(Study1TrainingConditionSummary(timing,modality,"Completed" if quick else self._condition_status_label(matching,completed,active),len(completed),len(matching),active["trial_id"] if active else None,matching[-1]["trial_id"] if matching else None))
        return out

    def study1_condition_status(self, participant_code: str, feedback_timing: FeedbackTiming, modality: Modality, *, practice: bool = False) -> Study1TrainingConditionSummary:
        for item in self.study1_condition_statuses(participant_code, practice=practice):
            if item.feedback_timing == feedback_timing and item.modality == modality:
                return item
        raise ValueError("Condition is not part of the compatibility Study 1 Gridworld matrix")

    def next_incomplete_study1_condition(self, participant_code: str, *, practice: bool = False):
        if practice:
            return self.next_incomplete_training_condition(participant_code)
        for item in self.study1_condition_statuses(participant_code, practice=False):
            if item.status != "Completed":
                return item
        return None

    # ---- Study 1 -------------------------------------------------------
    def study1_study_condition_statuses(self, participant_code: str) -> list[Study1StudyConditionSummary]:
        rows = self._db.experimental_conn.execute(
            """SELECT trial_id, environment, feedback_timing, modality, status, collection_status, created_at
               FROM trials WHERE participant_code=? AND study=? AND practice=0 ORDER BY created_at ASC""",
            (participant_code, Study.STUDY_1.value),
        ).fetchall()
        out=[]
        for req in STUDY1_STUDY_REQUIRED_CONDITIONS:
            matching=[r for r in rows if self._matches_study1_required(r,req)]
            completed=[r for r in matching if self._row_is_valid_completion(r)]
            active=next((r for r in reversed(matching) if r["status"] in _ACTIVE_TRIAL_STATUSES),None)
            last=matching[-1] if matching else None
            out.append(Study1StudyConditionSummary(
                req.key,req.label,req.environment,req.feedback_timing,self._condition_status_label(matching,completed,active),
                len(completed),len(matching),active["trial_id"] if active else None,last["trial_id"] if last else None,
                Modality(last["modality"]) if last else None,FeedbackTiming(last["feedback_timing"]) if last else None,
            ))
        return out

    def study1_study_condition_status(self, participant_code: str, key: str) -> Study1StudyConditionSummary:
        for item in self.study1_study_condition_statuses(participant_code):
            if item.key == key:
                return item
        raise ValueError(f"Unknown Study 1 condition: {key}")

    def next_incomplete_study1_study_condition(self, participant_code: str):
        for item in self.study1_study_condition_statuses(participant_code):
            if item.status != "Completed":
                return item
        return None

    @staticmethod
    def _matches_study1_required(row, req: Study1ProtocolCondition) -> bool:
        return row["environment"] == req.environment.value and row["feedback_timing"] == req.feedback_timing.value and row["modality"] in {m.value for m in req.allowed_modalities}

    # ---- Study 2 -------------------------------------------------------
    def study2_condition_statuses(self, participant_code: str) -> list[Study2ConditionSummary]:
        rows=self._db.experimental_conn.execute(
            """SELECT trial_id,feedback_timing,modality,status,collection_status,created_at FROM trials
               WHERE participant_code=? AND study=? AND environment=? AND practice=0 ORDER BY created_at ASC""",
            (participant_code,Study.STUDY_2.value,Environment.GRIDWORLD.value),
        ).fetchall()
        out=[]
        for modality in STUDY2_REQUIRED_MODALITIES:
            matching=[r for r in rows if r["modality"]==modality.value]
            completed=[r for r in matching if self._row_is_valid_completion(r)]
            active=next((r for r in reversed(matching) if r["status"] in _ACTIVE_TRIAL_STATUSES),None)
            last=matching[-1] if matching else None
            out.append(Study2ConditionSummary(modality,self._condition_status_label(matching,completed,active),len(completed),len(matching),active["trial_id"] if active else None,last["trial_id"] if last else None,FeedbackTiming(last["feedback_timing"]) if last else None))
        return out

    def study2_condition_status(self, participant_code: str, modality: Modality) -> Study2ConditionSummary:
        for item in self.study2_condition_statuses(participant_code):
            if item.modality == modality:
                return item
        raise ValueError("Modality is not part of Study 2")

    def next_incomplete_study2_condition(self, participant_code: str):
        for item in self.study2_condition_statuses(participant_code):
            if item.status != "Completed":
                return item
        return None

    def study2_finished(self, participant_code: str) -> bool:
        row=self._db.experimental_conn.execute(
            """SELECT 1 FROM workflow_runs WHERE participant_code=? AND step=? AND status=? AND notes=? LIMIT 1""",
            (participant_code,WorkflowStep.STUDY2_STUDY.value,StepRunStatus.COMPLETED.value,STUDY2_FINISHED_NOTE),
        ).fetchone()
        return row is not None

    def finish_study2(self, participant_code: str) -> StepRun:
        if self.study2_finished(participant_code):
            for run in reversed(self.list_runs(participant_code,WorkflowStep.STUDY2_STUDY)):
                if run.notes==STUDY2_FINISHED_NOTE and run.status==StepRunStatus.COMPLETED:
                    return run
        blocking=self.has_active_run(participant_code)
        if blocking is not None:
            raise ValueError(f"Finish or abort active run {blocking.run_id} first.")
        run,_=self.start_run(participant_code,WorkflowStep.STUDY2_STUDY)
        done=self.end_run(run.run_id,completed=True,notes=STUDY2_FINISHED_NOTE)
        assert done is not None
        return done

    # ---- Observation ---------------------------------------------------
    def observation_condition_statuses(self, participant_code: str) -> list[ObservationConditionSummary]:
        rows=self._db.experimental_conn.execute(
            """SELECT trial_id,environment,status,collection_status,created_at FROM trials
               WHERE participant_code=? AND study=? AND practice=0 ORDER BY created_at ASC""",
            (participant_code,Study.OBSERVATION.value),
        ).fetchall()
        out=[]
        for req in OBSERVATION_REQUIRED_CONDITIONS:
            matching=[r for r in rows if r["environment"]==req.environment.value]
            completed=[r for r in matching if self._row_is_valid_completion(r)]
            active=next((r for r in reversed(matching) if r["status"] in _ACTIVE_TRIAL_STATUSES),None)
            out.append(ObservationConditionSummary(req.key,req.label,req.environment,self._condition_status_label(matching,completed,active),len(completed),len(matching),active["trial_id"] if active else None,matching[-1]["trial_id"] if matching else None))
        return out

    def observation_condition_status(self, participant_code: str, key: str):
        for item in self.observation_condition_statuses(participant_code):
            if item.key==key:
                return item
        raise ValueError(f"Unknown observation condition: {key}")

    def next_incomplete_observation_condition(self, participant_code: str):
        for item in self.observation_condition_statuses(participant_code):
            if item.status!="Completed":
                return item
        return None

    # ---- Aggregate -----------------------------------------------------
    def get_run(self, run_id: str) -> Optional[StepRun]:
        row=self._db.experimental_conn.execute("SELECT * FROM workflow_runs WHERE run_id=?",(run_id,)).fetchone()
        return None if row is None else self._row_to_run(row)

    def list_runs(self, participant_code: str, step: Optional[WorkflowStep]=None) -> list[StepRun]:
        if step is None:
            rows=self._db.experimental_conn.execute("SELECT * FROM workflow_runs WHERE participant_code=? ORDER BY created_at ASC",(participant_code,)).fetchall()
        else:
            rows=self._db.experimental_conn.execute("SELECT * FROM workflow_runs WHERE participant_code=? AND step=? ORDER BY created_at ASC",(participant_code,step.value)).fetchall()
        return [self._row_to_run(r) for r in rows]

    def step_status(self, participant_code: str, step: WorkflowStep, *, participant_exists: bool=True) -> StepSummary:
        if step==WorkflowStep.REGISTRATION:
            status=StepOverallStatus.COMPLETED if participant_exists else StepOverallStatus.NOT_STARTED
            return StepSummary(step,status,1 if participant_exists else 0,1 if participant_exists else 0,None,None)
        runs=self.list_runs(participant_code,step)
        active=next((r for r in runs if r.status==StepRunStatus.IN_PROGRESS),None)
        if step==WorkflowStep.STUDY1_TRAINING:
            completed=sum(1 for x in self.training_condition_statuses(participant_code) if x.required and x.status=="Completed")
            complete=completed==STUDY1_TRAINING_REQUIRED_CONDITION_COUNT
        elif step==WorkflowStep.STUDY1_STUDY:
            completed=sum(1 for x in self.study1_study_condition_statuses(participant_code) if x.status=="Completed")
            complete=completed==STUDY1_STUDY_REQUIRED_CONDITION_COUNT
        elif step==WorkflowStep.STUDY2_STUDY:
            completed=sum(1 for x in self.study2_condition_statuses(participant_code) if x.status=="Completed")
            complete=self.study2_finished(participant_code) or completed==STUDY2_REQUIRED_CONDITION_COUNT
        elif step==WorkflowStep.AGENT_OBSERVATION:
            completed=sum(1 for x in self.observation_condition_statuses(participant_code) if x.status=="Completed")
            complete=completed==OBSERVATION_REQUIRED_CONDITION_COUNT
        else:  # hidden legacy Study 2 training
            completed=sum(1 for r in runs if r.status==StepRunStatus.COMPLETED)
            complete=completed>0
        if active is not None:
            overall=StepOverallStatus.IN_PROGRESS
        elif complete:
            overall=StepOverallStatus.COMPLETED
        elif runs or completed:
            overall=StepOverallStatus.IN_PROGRESS
        else:
            overall=StepOverallStatus.NOT_STARTED
        return StepSummary(step,overall,completed,len(runs),active,runs[-1] if runs else None)

    def all_step_statuses(self, participant_code: str, *, participant_exists: bool=True) -> list[StepSummary]:
        return [self.step_status(participant_code,s,participant_exists=participant_exists) for s in STEP_ORDER]

    def has_active_run(self, participant_code: str) -> Optional[StepRun]:
        steps=STEP_ORDER+[WorkflowStep.STUDY2_TRAINING]
        for step in steps:
            if step==WorkflowStep.REGISTRATION:
                continue
            for run in self.list_runs(participant_code,step):
                if run.status==StepRunStatus.IN_PROGRESS:
                    return run
        return None

    @staticmethod
    def _row_is_valid_completion(row) -> bool:
        if row["status"] != TrialStatus.COMPLETED.value:
            return False
        raw=row["collection_status"] if "collection_status" in row.keys() else None
        return raw in (None,"",CollectionRunStatus.PENDING.value,CollectionRunStatus.VALID.value)

    @staticmethod
    def _condition_status_label(matching, completed, active) -> str:
        if completed: return "Completed"
        if active is not None: return "In Progress"
        if matching: return "Needs Repeat"
        return "Not Started"

    def _insert(self, run: StepRun) -> None:
        self._db.experimental_conn.execute(
            """INSERT INTO workflow_runs (run_id,participant_code,step,study,practice,status,session_id,trial_id,created_at,started_at,ended_at,notes)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (run.run_id,run.participant_code,run.step.value,run.study.value if run.study else None,int(run.practice),run.status.value,run.session_id,run.trial_id,run.created_at,run.started_at,run.ended_at,run.notes),
        )
        self._db.experimental_conn.commit()

    def _persist(self, run: StepRun) -> None:
        self._db.experimental_conn.execute("UPDATE workflow_runs SET status=?,ended_at=?,notes=? WHERE run_id=?",(run.status.value,run.ended_at,run.notes,run.run_id))
        self._db.experimental_conn.commit()

    @staticmethod
    def _row_to_run(row) -> StepRun:
        return StepRun(
            run_id=row["run_id"],participant_code=row["participant_code"],step=WorkflowStep(row["step"]),
            study=Study(row["study"]) if row["study"] else None,practice=bool(row["practice"]),status=StepRunStatus(row["status"]),
            session_id=row["session_id"],trial_id=row["trial_id"],created_at=row["created_at"],started_at=row["started_at"],ended_at=row["ended_at"],notes=row["notes"] or "",
        )
