from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from remote.continuous_nav_client import ContinuousNavClient


def _trial(tmp_path: Path):
    root = tmp_path / "trial"
    for sub in ("events", "rl", "input"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    condition = SimpleNamespace(
        feedback_timing=SimpleNamespace(value="Requested Feedback"),
        modality=SimpleNamespace(value="Keyboard"),
    )
    return SimpleNamespace(
        trial_id="P001_S01_ST1ST_T03_R01",
        participant_code="P001",
        session_id="P001_S01",
        run_code="R01",
        trial_dir=str(root),
        condition=condition,
    )


def test_received_remote_state_gets_console_timestamp_and_csv(tmp_path: Path) -> None:
    client = ContinuousNavClient()
    trial = _trial(tmp_path)
    client.set_active_trial(trial)

    client._handle_raw(
        json.dumps(
            {
                "type": "STATE_UPDATE",
                "trial_id": trial.trial_id,
                "timestamp_utc_ns": 123,
                "monotonic_ns": 456,
                "episode": 7,
                "step": 18,
                "phase": "HUMAN",
                "robot_x": 1.2,
                "robot_y": 3.4,
                "robot_orientation": 0.5,
                "goal_x": 10.0,
                "goal_y": 11.0,
                "goal_radius": 1.0,
                "human_step": 2,
                "human_total_steps": 10,
            }
        )
    )

    events = (Path(trial.trial_dir) / "events" / "ubuntu_remote_events.jsonl").read_text()
    assert "console_receive_timestamp_utc_ns" in events

    state_csv = (Path(trial.trial_dir) / "rl" / "continuous_nav_state_stream.csv").read_text()
    assert "console_receive_timestamp_utc_ns" in state_csv
    assert "HUMAN" in state_csv
    assert "10.0" in state_csv


def test_worker_v1_manifest_checksum_is_tolerated_after_finalize(tmp_path: Path) -> None:
    import hashlib

    extract = tmp_path / "extracted"
    extract.mkdir()
    data = extract / "rl_steps.csv"
    data.write_text("a,b\n1,2\n", encoding="utf-8")
    manifest = extract / "manifest.json"
    manifest.write_text('{"status":"FINALIZED"}', encoding="utf-8")

    good = hashlib.sha256(data.read_bytes()).hexdigest()
    (extract / "checksums.json").write_text(
        json.dumps(
            {
                "algorithm": "sha256",
                "files": {
                    "rl_steps.csv": good,
                    # Ubuntu worker v1 hashes manifest before its final status update.
                    "manifest.json": "0" * 64,
                },
            }
        ),
        encoding="utf-8",
    )

    ContinuousNavClient._verify_extracted_checksums(extract)
