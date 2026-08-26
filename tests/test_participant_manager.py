from __future__ import annotations

from pathlib import Path

import pytest

from core.database import Database
from core.participant_manager import ParticipantManager


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "identifiable.sqlite3", tmp_path / "experimental.sqlite3")


@pytest.fixture()
def manager(db: Database) -> ParticipantManager:
    return ParticipantManager(db)


def test_create_participant_generates_sequential_codes(manager: ParticipantManager) -> None:
    p1 = manager.create_participant(name="Alice")
    p2 = manager.create_participant(name="Bob")
    assert p1.participant_code == "P001"
    assert p2.participant_code == "P002"


def test_create_participant_requires_name(manager: ParticipantManager) -> None:
    with pytest.raises(ValueError):
        manager.create_participant(name="   ")


def test_pii_is_not_stored_in_experimental_db(manager: ParticipantManager, db: Database) -> None:
    manager.create_participant(name="Carol Jones", email="carol@example.com", phone="555-1234")

    # The experimental DB's participants table must never contain a name,
    # email, or phone column/value -- only the pseudonymous code.
    row = db.experimental_conn.execute(
        "SELECT * FROM participants WHERE participant_code = 'P001'"
    ).fetchone()
    assert row is not None
    assert set(row.keys()) == {
        "participant_code",
        "created_at",
        "demographics",
        "notes",
        "modality_order",
    }
    dumped = str(dict(row))
    assert "Carol" not in dumped
    assert "carol@example.com" not in dumped


def test_identity_is_retrievable_from_identifiable_db(manager: ParticipantManager) -> None:
    manager.create_participant(name="Dana Lee", email="dana@example.com", phone="555-6789")
    identity = manager.get_identity("P001")
    assert identity is not None
    assert identity.name == "Dana Lee"
    assert identity.email == "dana@example.com"


def test_list_participants_joins_name_from_identifiable_db(manager: ParticipantManager) -> None:
    manager.create_participant(name="Eve Adams")
    listed = manager.list_participants()
    assert len(listed) == 1
    assert listed[0]["participant_code"] == "P001"
    assert listed[0]["name"] == "Eve Adams"


def test_search_matches_code_or_name(manager: ParticipantManager) -> None:
    manager.create_participant(name="Frank Sinatra")
    manager.create_participant(name="Grace Hopper")

    assert [p["participant_code"] for p in manager.search("hopper")] == ["P002"]
    assert [p["participant_code"] for p in manager.search("P001")] == ["P001"]
    assert len(manager.search("")) == 2
