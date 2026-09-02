"""SQLite database access.

Per spec section 8, identifiable participant information (name/email/phone)
is stored in a completely separate database file from all experimental
metadata (sessions, trials, conditions), which is keyed only by the
pseudonymous participant_code. Nothing should ever join these two databases
together at runtime -- that would defeat the point of separating them.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)


_IDENTIFIABLE_SCHEMA = """
CREATE TABLE IF NOT EXISTS participants (
    participant_code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT,
    phone TEXT,
    created_at REAL NOT NULL
);
"""

_EXPERIMENTAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS participants (
    participant_code TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    demographics TEXT,
    notes TEXT,
    modality_order TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    participant_code TEXT NOT NULL,
    study TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at REAL NOT NULL,
    started_at REAL,
    ended_at REAL,
    session_dir TEXT,
    session_max_minutes INTEGER,
    continuous_task_max_minutes INTEGER,
    modality_order TEXT,
    notes TEXT,
    FOREIGN KEY (participant_code) REFERENCES participants (participant_code)
);

CREATE TABLE IF NOT EXISTS trials (
    trial_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    participant_code TEXT NOT NULL,
    study TEXT NOT NULL,
    environment TEXT NOT NULL,
    feedback_timing TEXT,
    modality TEXT NOT NULL,
    practice INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    random_seed INTEGER,
    order_index INTEGER,
    created_at REAL NOT NULL,
    started_at REAL,
    ended_at REAL,
    trial_dir TEXT,
    condition_code TEXT,
    run_code TEXT,
    condition_name TEXT,
    collection_status TEXT NOT NULL DEFAULT 'Pending',
    repeat_reason TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (session_id) REFERENCES sessions (session_id)
);

-- Tracks one attempt/run of a WorkflowStep (Registration, Study 1/2
-- Training, Study 1/2 Study) for a participant. A step can have many runs
-- (repeats); WorkflowManager aggregates these into a step-level status.
CREATE TABLE IF NOT EXISTS workflow_runs (
    run_id TEXT PRIMARY KEY,
    participant_code TEXT NOT NULL,
    step TEXT NOT NULL,
    study TEXT,
    practice INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    session_id TEXT,
    trial_id TEXT,
    created_at REAL NOT NULL,
    started_at REAL,
    ended_at REAL,
    notes TEXT,
    FOREIGN KEY (participant_code) REFERENCES participants (participant_code)
);

-- Explicit researcher overrides. These never fabricate a Trial or sensor data;
-- they only allow a workflow phase/condition to be treated as complete.
CREATE TABLE IF NOT EXISTS completion_overrides (
    override_id INTEGER PRIMARY KEY AUTOINCREMENT,
    participant_code TEXT NOT NULL,
    step TEXT NOT NULL,
    item_key TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at REAL NOT NULL,
    UNIQUE (participant_code, step, item_key),
    FOREIGN KEY (participant_code) REFERENCES participants (participant_code)
);
"""


class Database:
    """Owns the two SQLite connections used by the app.

    A tiny wrapper rather than an ORM: the schema is small and stable, and
    researchers may want to inspect the .sqlite3 files directly.
    """

    def __init__(self, identifiable_db_path: Path, experimental_db_path: Path) -> None:
        self.identifiable_db_path = Path(identifiable_db_path)
        self.experimental_db_path = Path(experimental_db_path)
        self.identifiable_db_path.parent.mkdir(parents=True, exist_ok=True)
        self.experimental_db_path.parent.mkdir(parents=True, exist_ok=True)

        self.identifiable_conn = sqlite3.connect(
            str(self.identifiable_db_path), check_same_thread=False
        )
        self.identifiable_conn.row_factory = sqlite3.Row

        self.experimental_conn = sqlite3.connect(
            str(self.experimental_db_path), check_same_thread=False
        )
        self.experimental_conn.row_factory = sqlite3.Row

        self._init_schema()

    def _init_schema(self) -> None:
        self.identifiable_conn.executescript(_IDENTIFIABLE_SCHEMA)
        self.identifiable_conn.commit()
        self.experimental_conn.executescript(_EXPERIMENTAL_SCHEMA)
        self._migrate_experimental_schema()
        self.experimental_conn.commit()
        logger.debug(
            "Database schema ready (identifiable=%s, experimental=%s)",
            self.identifiable_db_path,
            self.experimental_db_path,
        )


    def _migrate_experimental_schema(self) -> None:
        """Add v0.9 naming columns to databases created by older builds."""
        existing = {
            row["name"]
            for row in self.experimental_conn.execute("PRAGMA table_info(trials)").fetchall()
        }
        additions = {
            "condition_code": "TEXT",
            "run_code": "TEXT",
            "condition_name": "TEXT",
            "collection_status": "TEXT NOT NULL DEFAULT 'Pending'",
            "repeat_reason": "TEXT NOT NULL DEFAULT ''",
        }
        for name, ddl in additions.items():
            if name not in existing:
                self.experimental_conn.execute(
                    f"ALTER TABLE trials ADD COLUMN {name} {ddl}"
                )

    def close(self) -> None:
        self.identifiable_conn.close()
        self.experimental_conn.close()
