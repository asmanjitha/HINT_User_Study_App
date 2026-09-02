"""Participant collection-session lifecycle manager."""

from __future__ import annotations

import json
import logging
import time
from typing import Optional

import yaml

from core.config_loader import AppConfig
from core.data_naming import session_short_code
from core.database import Database
from core.event_bus import EventBus
from core.id_generator import generate_session_id
from models.enums import EventType, SessionStatus, Study
from models.event import StudyEvent
from models.session import Session

logger = logging.getLogger(__name__)


class SessionManager:
    def __init__(self, db: Database, config: AppConfig, event_bus: EventBus) -> None:
        self._db = db
        self._config = config
        self._event_bus = event_bus

    def create_session(
        self,
        participant_code: str,
        study: Study = Study.COMBINED_SESSION,
    ) -> Session:
        """Create one participant collection session.

        ``study`` is accepted for backward compatibility, but new workflow
        sessions use one combined participant session spanning Training,
        Study 1, Study 2, and the final Agent Observation phase.
        """
        session_id = generate_session_id(self._db, participant_code)
        scope = Study.COMBINED_SESSION

        session = Session(
            session_id=session_id,
            participant_code=participant_code,
            study=scope,
            session_max_minutes=self._config.session_max_minutes,
            continuous_task_max_minutes=self._config.continuous_task_max_minutes,
        )

        short = session_short_code(session_id)
        session_dir = self._config.data_dir / participant_code / short
        session_dir.mkdir(parents=True, exist_ok=True)
        session.session_dir = str(session_dir)

        with open(session_dir / "configuration.yaml", "w", encoding="utf-8") as f:
            yaml.safe_dump(self._config.study_raw, f, sort_keys=False)

        with open(session_dir / "session.json", "w", encoding="utf-8") as f:
            json.dump(session.to_metadata_dict(), f, indent=2)

        self._db.experimental_conn.execute(
            "INSERT INTO sessions "
            "(session_id, participant_code, study, status, created_at, started_at, ended_at, "
            " session_dir, session_max_minutes, continuous_task_max_minutes, modality_order, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session.session_id,
                session.participant_code,
                session.study.value,
                session.status.value,
                session.created_at,
                session.started_at,
                session.ended_at,
                session.session_dir,
                session.session_max_minutes,
                session.continuous_task_max_minutes,
                session.modality_order,
                session.notes,
            ),
        )
        self._db.experimental_conn.commit()

        logger.info("Created participant session %s for %s", session_id, participant_code)
        self._event_bus.publish(
            StudyEvent(
                event_type=EventType.SESSION_CREATED,
                participant_id=participant_code,
                session_id=session_id,
            )
        )
        return session

    def get_or_create_active_session(self, participant_code: str) -> Session:
        """Reuse S01 across the full participant workflow until it is closed."""
        row = self._db.experimental_conn.execute(
            """
            SELECT * FROM sessions
            WHERE participant_code = ?
              AND status IN (?, ?, ?)
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (
                participant_code,
                SessionStatus.CREATED.value,
                SessionStatus.IN_PROGRESS.value,
                SessionStatus.PAUSED.value,
            ),
        ).fetchone()
        if row is not None:
            session = self._row_to_session(row)
            if session.status == SessionStatus.CREATED:
                self.start_session(session)
            return session

        session = self.create_session(participant_code)
        self.start_session(session)
        return session

    def preview_session_id(self, participant_code: str) -> str:
        row = self._db.experimental_conn.execute(
            """
            SELECT session_id FROM sessions
            WHERE participant_code = ?
              AND status IN (?, ?, ?)
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (
                participant_code,
                SessionStatus.CREATED.value,
                SessionStatus.IN_PROGRESS.value,
                SessionStatus.PAUSED.value,
            ),
        ).fetchone()
        return row["session_id"] if row is not None else generate_session_id(self._db, participant_code)

    def start_session(self, session: Session) -> None:
        if session.status == SessionStatus.IN_PROGRESS:
            return
        session.status = SessionStatus.IN_PROGRESS
        if session.started_at is None:
            session.started_at = time.time()
        self._persist_status(session)
        self._event_bus.publish(
            StudyEvent(
                event_type=EventType.SESSION_STARTED,
                participant_id=session.participant_code,
                session_id=session.session_id,
            )
        )

    def end_session(self, session: Session, aborted: bool = False) -> None:
        session.status = SessionStatus.ABORTED if aborted else SessionStatus.COMPLETED
        session.ended_at = time.time()
        self._persist_status(session)
        self._event_bus.publish(
            StudyEvent(
                event_type=EventType.SESSION_ENDED,
                participant_id=session.participant_code,
                session_id=session.session_id,
                value=session.status.value,
            )
        )

    def _persist_status(self, session: Session) -> None:
        self._db.experimental_conn.execute(
            "UPDATE sessions SET status = ?, started_at = ?, ended_at = ? WHERE session_id = ?",
            (session.status.value, session.started_at, session.ended_at, session.session_id),
        )
        self._db.experimental_conn.commit()
        if session.session_path is not None:
            with open(session.session_path / "session.json", "w", encoding="utf-8") as f:
                json.dump(session.to_metadata_dict(), f, indent=2)

    def get_session(self, session_id: str) -> Optional[Session]:
        row = self._db.experimental_conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        return None if row is None else self._row_to_session(row)

    def list_sessions(self) -> list[Session]:
        rows = self._db.experimental_conn.execute(
            "SELECT * FROM sessions ORDER BY created_at DESC"
        ).fetchall()
        return [self._row_to_session(row) for row in rows]

    def list_sessions_for_participant(self, participant_code: str) -> list[Session]:
        rows = self._db.experimental_conn.execute(
            "SELECT * FROM sessions WHERE participant_code = ? ORDER BY created_at DESC",
            (participant_code,),
        ).fetchall()
        return [self._row_to_session(row) for row in rows]

    @staticmethod
    def _row_to_session(row) -> Session:
        try:
            scope = Study(row["study"])
        except ValueError:
            # Older v0.8 rows used Study 1/Study 2 as the session scope.
            scope = Study.COMBINED_SESSION
        return Session(
            session_id=row["session_id"],
            participant_code=row["participant_code"],
            study=scope,
            status=SessionStatus(row["status"]),
            created_at=row["created_at"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            session_max_minutes=row["session_max_minutes"],
            continuous_task_max_minutes=row["continuous_task_max_minutes"],
            session_dir=row["session_dir"],
            modality_order=row["modality_order"],
            notes=row["notes"] or "",
        )
