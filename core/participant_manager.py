"""Participant Manager (spec section 8).

Owns both databases and is the *only* piece of code allowed to write PII.
Everything downstream of here (sessions, trials, RL, recording, GUI pages
other than the Participants page) should only ever see a participant_code
string or a ParticipantRecord (no PII fields).
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from core.database import Database
from core.id_generator import generate_participant_code
from models.participant import ParticipantIdentity, ParticipantRecord

logger = logging.getLogger(__name__)


class ParticipantManager:
    def __init__(self, db: Database) -> None:
        self._db = db

    def create_participant(
        self,
        name: str,
        email: str = "",
        phone: str = "",
        age: Optional[int] = None,
    ) -> ParticipantRecord:
        """Create a new participant, generating a pseudonymous code.

        Writes the identity (name/email/phone) to the identifiable DB and a
        PII-free record to the experimental DB, in a single call so the two
        never drift out of sync. Age is study-relevant but not identifying
        on its own (per the IRB protocol's pseudo-anonymization plan, it is
        used only in aggregate), so it's stored in ``demographics`` on the
        experimental record -- never alongside name/email/phone.
        """
        if not name.strip():
            raise ValueError("Participant name is required.")

        code = generate_participant_code(self._db)
        identity = ParticipantIdentity(participant_code=code, name=name.strip(), email=email.strip(), phone=phone.strip())
        demographics = {"age": age} if age is not None else {}
        record = ParticipantRecord(participant_code=code, demographics=demographics)

        self._db.identifiable_conn.execute(
            "INSERT INTO participants (participant_code, name, email, phone, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (identity.participant_code, identity.name, identity.email, identity.phone, identity.created_at),
        )
        self._db.identifiable_conn.commit()

        self._db.experimental_conn.execute(
            "INSERT INTO participants (participant_code, created_at, demographics, notes, modality_order) "
            "VALUES (?, ?, ?, ?, ?)",
            (record.participant_code, record.created_at, json.dumps(record.demographics), record.notes, record.modality_order),
        )
        self._db.experimental_conn.commit()

        logger.info("Created participant %s", code)
        return record

    def update_demographics(self, participant_code: str, **fields) -> None:
        """Merge ``fields`` (e.g. age=34) into the participant's demographics."""
        record = self.get_record(participant_code)
        if record is None:
            raise ValueError(f"Unknown participant code: {participant_code}")
        demographics = dict(record.demographics)
        demographics.update(fields)
        self._db.experimental_conn.execute(
            "UPDATE participants SET demographics = ? WHERE participant_code = ?",
            (json.dumps(demographics), participant_code),
        )
        self._db.experimental_conn.commit()

    def edit_identity(
        self, participant_code: str, name: Optional[str] = None, email: Optional[str] = None, phone: Optional[str] = None
    ) -> None:
        current = self.get_identity(participant_code)
        if current is None:
            raise ValueError(f"Unknown participant code: {participant_code}")

        new_name = name if name is not None else current.name
        new_email = email if email is not None else current.email
        new_phone = phone if phone is not None else current.phone

        self._db.identifiable_conn.execute(
            "UPDATE participants SET name = ?, email = ?, phone = ? WHERE participant_code = ?",
            (new_name, new_email, new_phone, participant_code),
        )
        self._db.identifiable_conn.commit()
        logger.info("Updated identity for %s", participant_code)

    def get_identity(self, participant_code: str) -> Optional[ParticipantIdentity]:
        row = self._db.identifiable_conn.execute(
            "SELECT * FROM participants WHERE participant_code = ?", (participant_code,)
        ).fetchone()
        if row is None:
            return None
        return ParticipantIdentity(
            participant_code=row["participant_code"],
            name=row["name"],
            email=row["email"] or "",
            phone=row["phone"] or "",
            created_at=row["created_at"],
        )

    def get_record(self, participant_code: str) -> Optional[ParticipantRecord]:
        row = self._db.experimental_conn.execute(
            "SELECT * FROM participants WHERE participant_code = ?", (participant_code,)
        ).fetchone()
        if row is None:
            return None
        return ParticipantRecord(
            participant_code=row["participant_code"],
            created_at=row["created_at"],
            demographics=json.loads(row["demographics"]) if row["demographics"] else {},
            notes=row["notes"] or "",
            modality_order=row["modality_order"],
        )

    def list_participants(self) -> list[dict]:
        """List participants with display fields (name comes from the
        identifiable DB, joined in Python -- never via a SQL join across
        the two database files)."""
        exp_rows = self._db.experimental_conn.execute(
            "SELECT participant_code, created_at FROM participants ORDER BY participant_code"
        ).fetchall()

        results = []
        for row in exp_rows:
            identity = self.get_identity(row["participant_code"])
            record = self.get_record(row["participant_code"])
            session_count = self._db.experimental_conn.execute(
                "SELECT COUNT(*) as n FROM sessions WHERE participant_code = ?",
                (row["participant_code"],),
            ).fetchone()["n"]
            results.append(
                {
                    "participant_code": row["participant_code"],
                    "name": identity.name if identity else "",
                    "email": identity.email if identity else "",
                    "age": (record.demographics.get("age") if record else None),
                    "created_at": row["created_at"],
                    "sessions_completed": session_count,
                }
            )
        return results

    def search(self, query: str) -> list[dict]:
        query = query.strip().lower()
        if not query:
            return self.list_participants()
        return [
            p
            for p in self.list_participants()
            if query in p["participant_code"].lower() or query in p["name"].lower()
        ]
