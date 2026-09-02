"""Trial lifecycle manager with readable T##/R## storage naming."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from core.data_naming import (
    allocate_trial_storage_identity,
    preview_trial_storage_identity,
    session_short_code,
)
from core.database import Database
from core.event_bus import EventBus
from models.enums import (
    CollectionRunStatus,
    Environment,
    EventType,
    FeedbackTiming,
    Modality,
    Study,
    TrialStatus,
)
from models.event import StudyEvent
from models.session import Session
from models.trial import ExperimentCondition, Trial

_TRIAL_SUBDIRS = ("rl", "sensors", "input", "events", "logs")


class TrialManager:
    def __init__(self, db: Database, event_bus: EventBus) -> None:
        self._db = db
        self._event_bus = event_bus

    def create_trial(
        self,
        session: Session,
        condition: ExperimentCondition,
        practice: bool = False,
    ) -> Trial:
        if session.session_path is None:
            raise ValueError("Session has no storage directory")
        if condition.study not in (Study.STUDY_1, Study.STUDY_2, Study.OBSERVATION):
            raise ValueError("Trials must belong to Study 1, Study 2, or Agent Observation")

        identity = allocate_trial_storage_identity(
            self._db,
            session_id=session.session_id,
            condition=condition,
            practice=practice,
        )
        trial_dir = session.session_path / identity.relative_dir
        for subdir in _TRIAL_SUBDIRS:
            (trial_dir / subdir).mkdir(parents=True, exist_ok=True)

        trial = Trial(
            trial_id=identity.trial_id,
            session_id=session.session_id,
            participant_code=session.participant_code,
            condition=condition,
            practice=practice,
            condition_code=identity.condition_code,
            run_code=identity.run_code,
            condition_name=identity.condition_name,
            collection_status=CollectionRunStatus.PENDING,
            trial_dir=str(trial_dir),
        )
        self._write_metadata(trial)

        self._db.experimental_conn.execute(
            """
            INSERT INTO trials
            (
                trial_id, session_id, participant_code, study, environment,
                feedback_timing, modality, practice, status, random_seed,
                order_index, created_at, started_at, ended_at, trial_dir,
                condition_code, run_code, condition_name, collection_status,
                repeat_reason
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trial.trial_id,
                trial.session_id,
                trial.participant_code,
                condition.study.value,
                condition.environment.value,
                condition.feedback_timing.value,
                condition.modality.value,
                int(trial.practice),
                trial.status.value,
                condition.random_seed,
                condition.order_index,
                trial.created_at,
                trial.started_at,
                trial.ended_at,
                trial.trial_dir,
                trial.condition_code,
                trial.run_code,
                trial.condition_name,
                trial.collection_status.value,
                trial.repeat_reason,
            ),
        )
        self._db.experimental_conn.commit()

        self._event_bus.publish(
            StudyEvent(
                event_type=EventType.TRIAL_CREATED,
                participant_id=trial.participant_code,
                session_id=trial.session_id,
                trial_id=trial.trial_id,
                value=(
                    f"{'practice' if practice else 'experimental'}; "
                    f"{trial.condition_code}/{trial.run_code}; "
                    f"{trial.condition_name}"
                ),
            )
        )
        return trial

    def preview_storage(
        self,
        *,
        participant_code: str,
        session_id: str,
        condition: ExperimentCondition,
        practice: bool = False,
    ) -> dict[str, str]:
        identity = preview_trial_storage_identity(
            self._db,
            session_id=session_id,
            condition=condition,
            practice=practice,
        )
        return {
            "condition_code": identity.condition_code,
            "run_code": identity.run_code,
            "condition_name": identity.condition_name,
            "trial_id": identity.trial_id,
            "relative_dir": str(Path(participant_code) / session_short_code(session_id) / identity.relative_dir),
        }

    def start_trial(self, trial: Trial) -> None:
        trial.status = TrialStatus.PRACTICE if trial.practice else TrialStatus.RUNNING
        trial.started_at = time.time()
        self._persist(trial)
        self._event_bus.publish(
            StudyEvent(
                event_type=EventType.TRIAL_STARTED,
                participant_id=trial.participant_code,
                session_id=trial.session_id,
                trial_id=trial.trial_id,
                value=f"{trial.condition_code}/{trial.run_code}",
            )
        )
        if trial.practice:
            self._event_bus.publish(
                StudyEvent(
                    event_type=EventType.PRACTICE_STARTED,
                    participant_id=trial.participant_code,
                    session_id=trial.session_id,
                    trial_id=trial.trial_id,
                )
            )

    def pause_trial(self, trial: Trial) -> None:
        trial.status = TrialStatus.PAUSED
        self._persist(trial)
        self._event_bus.publish(
            StudyEvent(
                event_type=EventType.TRIAL_PAUSED,
                participant_id=trial.participant_code,
                session_id=trial.session_id,
                trial_id=trial.trial_id,
            )
        )

    def resume_trial(self, trial: Trial) -> None:
        trial.status = TrialStatus.PRACTICE if trial.practice else TrialStatus.RUNNING
        self._persist(trial)
        self._event_bus.publish(
            StudyEvent(
                event_type=EventType.TRIAL_RESUMED,
                participant_id=trial.participant_code,
                session_id=trial.session_id,
                trial_id=trial.trial_id,
            )
        )

    def end_trial(
        self,
        trial: Trial,
        completed: bool = True,
        *,
        collection_status: CollectionRunStatus | None = None,
        repeat_reason: str = "",
    ) -> None:
        if collection_status is None:
            collection_status = (
                CollectionRunStatus.VALID if completed else CollectionRunStatus.ABORTED
            )
        trial.status = TrialStatus.COMPLETED if completed else TrialStatus.STOPPED
        trial.collection_status = collection_status
        trial.repeat_reason = repeat_reason.strip()
        trial.ended_at = time.time()
        self._persist(trial)

        if trial.practice:
            self._event_bus.publish(
                StudyEvent(
                    event_type=EventType.PRACTICE_ENDED,
                    participant_id=trial.participant_code,
                    session_id=trial.session_id,
                    trial_id=trial.trial_id,
                )
            )
        self._event_bus.publish(
            StudyEvent(
                event_type=EventType.TRIAL_ENDED,
                participant_id=trial.participant_code,
                session_id=trial.session_id,
                trial_id=trial.trial_id,
                value=(
                    f"{trial.collection_status.value}; "
                    f"{trial.condition_code}/{trial.run_code}"
                    + (f"; reason={trial.repeat_reason}" if trial.repeat_reason else "")
                ),
            )
        )

    def get_trial(self, trial_id: str) -> Optional[Trial]:
        row = self._db.experimental_conn.execute(
            "SELECT * FROM trials WHERE trial_id = ?", (trial_id,)
        ).fetchone()
        return None if row is None else self._row_to_trial(row)

    def list_trials(
        self,
        participant_code: str,
        *,
        study: Study | None = None,
        practice: bool | None = None,
    ) -> list[Trial]:
        clauses = ["participant_code = ?"]
        args: list[object] = [participant_code]
        if study is not None:
            clauses.append("study = ?")
            args.append(study.value)
        if practice is not None:
            clauses.append("practice = ?")
            args.append(int(practice))
        rows = self._db.experimental_conn.execute(
            f"SELECT * FROM trials WHERE {' AND '.join(clauses)} ORDER BY created_at ASC",
            tuple(args),
        ).fetchall()
        return [self._row_to_trial(row) for row in rows]

    def _persist(self, trial: Trial) -> None:
        self._db.experimental_conn.execute(
            """
            UPDATE trials
            SET status = ?, started_at = ?, ended_at = ?,
                collection_status = ?, repeat_reason = ?
            WHERE trial_id = ?
            """,
            (
                trial.status.value,
                trial.started_at,
                trial.ended_at,
                trial.collection_status.value,
                trial.repeat_reason,
                trial.trial_id,
            ),
        )
        self._db.experimental_conn.commit()
        self._write_metadata(trial)

    @staticmethod
    def _write_metadata(trial: Trial) -> None:
        if trial.trial_path is None:
            return
        path = trial.trial_path / "trial.json"
        with path.open("w", encoding="utf-8") as f:
            json.dump(trial.to_metadata_dict(), f, indent=2)

    @staticmethod
    def _row_to_trial(row) -> Trial:
        condition = ExperimentCondition(
            study=Study(row["study"]),
            environment=Environment(row["environment"]),
            feedback_timing=FeedbackTiming(row["feedback_timing"]),
            modality=Modality(row["modality"]),
            random_seed=row["random_seed"],
            order_index=row["order_index"],
        )
        raw_collection = row["collection_status"] if "collection_status" in row.keys() else None
        if raw_collection in {item.value for item in CollectionRunStatus}:
            collection_status = CollectionRunStatus(raw_collection)
        elif row["status"] == TrialStatus.COMPLETED.value:
            collection_status = CollectionRunStatus.VALID
        elif row["status"] == TrialStatus.STOPPED.value:
            collection_status = CollectionRunStatus.ABORTED
        else:
            collection_status = CollectionRunStatus.PENDING

        return Trial(
            trial_id=row["trial_id"],
            session_id=row["session_id"],
            participant_code=row["participant_code"],
            condition=condition,
            practice=bool(row["practice"]),
            status=TrialStatus(row["status"]),
            collection_status=collection_status,
            condition_code=(row["condition_code"] or "") if "condition_code" in row.keys() else "",
            run_code=(row["run_code"] or "") if "run_code" in row.keys() else "",
            condition_name=(row["condition_name"] or "") if "condition_name" in row.keys() else "",
            repeat_reason=(row["repeat_reason"] or "") if "repeat_reason" in row.keys() else "",
            created_at=row["created_at"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            trial_dir=row["trial_dir"],
        )
