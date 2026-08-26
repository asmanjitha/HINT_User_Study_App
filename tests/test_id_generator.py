from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from core.database import Database
from core.id_generator import generate_participant_code, generate_session_id, generate_trial_id


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "identifiable.sqlite3", tmp_path / "experimental.sqlite3")


def test_first_participant_code_is_p001(db: Database) -> None:
    assert generate_participant_code(db) == "P001"


def test_participant_code_increments(db: Database) -> None:
    db.experimental_conn.execute(
        "INSERT INTO participants (participant_code, created_at) VALUES (?, ?)",
        ("P001", 0.0),
    )
    db.experimental_conn.execute(
        "INSERT INTO participants (participant_code, created_at) VALUES (?, ?)",
        ("P002", 0.0),
    )
    db.experimental_conn.commit()
    assert generate_participant_code(db) == "P003"


def test_participant_code_fills_gap_by_using_max_plus_one(db: Database) -> None:
    # Deleted participants shouldn't cause ID reuse -- always max+1.
    db.experimental_conn.execute(
        "INSERT INTO participants (participant_code, created_at) VALUES (?, ?)",
        ("P005", 0.0),
    )
    db.experimental_conn.commit()
    assert generate_participant_code(db) == "P006"


def test_first_session_id_format(db: Database) -> None:
    assert generate_session_id(db, "P023") == "P023_S01"


def test_session_id_increments_per_participant_only(db: Database) -> None:
    for pcode, sid in [("P023", "P023_S01"), ("P023", "P023_S02"), ("P099", "P099_S01")]:
        db.experimental_conn.execute(
            "INSERT INTO sessions (session_id, participant_code, study, status, created_at) "
            "VALUES (?, ?, 'Study 1', 'Created', 0.0)",
            (sid, pcode),
        )
    db.experimental_conn.commit()

    assert generate_session_id(db, "P023") == "P023_S03"
    assert generate_session_id(db, "P099") == "P099_S02"
    assert generate_session_id(db, "P001") == "P001_S01"


def test_first_trial_id_format(db: Database) -> None:
    assert generate_trial_id(db, "P023_S01") == "P023_S01_T001"


def test_trial_id_increments_per_session_only(db: Database) -> None:
    for tid, sid in [
        ("P023_S01_T001", "P023_S01"),
        ("P023_S01_T002", "P023_S01"),
        ("P023_S02_T001", "P023_S02"),
    ]:
        db.experimental_conn.execute(
            "INSERT INTO trials (trial_id, session_id, participant_code, study, environment, "
            "modality, status, created_at) VALUES (?, ?, 'P023', 'Study 1', 'Gridworld', "
            "'Keyboard', 'Created', 0.0)",
            (tid, sid),
        )
    db.experimental_conn.commit()

    assert generate_trial_id(db, "P023_S01") == "P023_S01_T003"
    assert generate_trial_id(db, "P023_S02") == "P023_S02_T002"
