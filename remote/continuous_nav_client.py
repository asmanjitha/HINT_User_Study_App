"""Persistent client for the Ubuntu HINT continuous-navigation worker.

The Ubuntu worker owns the simulator/RL state.  This client owns only the
network connection, console-side timestamps/logs, and trial-bundle transfer.
It deliberately runs the WebSocket connection in a standard Python thread so
PySide's GUI thread never owns an asyncio event loop.
"""

from __future__ import annotations

import csv
import json
import logging
import queue
import statistics
import threading
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import requests
from PySide6.QtCore import QObject, Signal

logger = logging.getLogger(__name__)


@dataclass
class _Waiter:
    expected: set[str]
    event: threading.Event
    response: dict[str, Any] | None = None
    error: str = ""


class ContinuousNavClient(QObject):
    """Threaded WebSocket client for the Ubuntu continuous-navigation worker."""

    connection_status_changed = Signal(str)
    message_received = Signal(object)
    trial_prepared = Signal(object)
    task_started = Signal(object)
    task_ended = Signal(object)
    state_updated = Signal(object)
    collision = Signal(object)
    human_action_requested = Signal(object)
    human_action_applied = Signal(object)
    episode_started = Signal(object)
    episode_ended = Signal(object)
    remote_error = Signal(str)

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8765,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._host = str(host).strip() or "127.0.0.1"
        self._port = int(port)
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._connected_event = threading.Event()
        self._send_q: queue.Queue[dict[str, Any]] = queue.Queue()
        self._waiters: list[_Waiter] = []
        self._waiter_lock = threading.Lock()
        self._socket = None
        self._connection_error = ""
        self._status: dict[str, Any] = {}
        self._clock_sync: dict[str, Any] = {}

        self._active_trial_id: str | None = None
        self._trial_path: Path | None = None
        self._log_lock = threading.Lock()
        self._remote_event_path: Path | None = None
        self._state_csv_path: Path | None = None
        self._action_csv_path: Path | None = None

    # ------------------------------------------------------------------
    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        return self._port

    @property
    def websocket_uri(self) -> str:
        return f"ws://{self._host}:{self._port}/ws"

    @property
    def http_base(self) -> str:
        return f"http://{self._host}:{self._port}"

    @property
    def connected(self) -> bool:
        return self._connected_event.is_set() and self._thread is not None and self._thread.is_alive()

    @property
    def status_snapshot(self) -> dict[str, Any]:
        return dict(self._status)

    @property
    def clock_sync(self) -> dict[str, Any]:
        return dict(self._clock_sync)

    def configure(self, host: str, port: int | None = None) -> None:
        host = str(host).strip()
        if not host:
            raise ValueError("Ubuntu worker host/IP cannot be blank")
        port = self._port if port is None else int(port)
        if self.connected and (host != self._host or port != self._port):
            raise RuntimeError("Disconnect the current Ubuntu worker before changing its address")
        self._host = host
        self._port = port

    # ------------------------------------------------------------------
    def connect_worker(self, timeout: float = 6.0, *, measure_clock: bool = True) -> dict[str, Any]:
        if self.connected:
            if measure_clock and not self._clock_sync:
                self.measure_clock_offset()
            return self.status_snapshot

        self._connection_error = ""
        self._stop_event.clear()
        self._connected_event.clear()
        self.connection_status_changed.emit(f"Connecting to {self._host}:{self._port}…")
        self._thread = threading.Thread(target=self._thread_main, name="hint-ubuntu-worker", daemon=True)
        self._thread.start()
        if not self._connected_event.wait(timeout):
            message = self._connection_error or f"Timed out connecting to {self.websocket_uri}"
            self.disconnect_worker()
            raise RuntimeError(message)
        status = self.get_status(timeout=timeout)
        if measure_clock:
            try:
                self.measure_clock_offset(samples=5, timeout=min(timeout, 3.0))
            except Exception as exc:
                logger.warning("Ubuntu clock-offset measurement failed: %s", exc)
        return status

    def disconnect_worker(self) -> None:
        self._stop_event.set()
        sock = self._socket
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass
        thread = self._thread
        if thread is not None and thread.is_alive() and threading.current_thread() is not thread:
            thread.join(timeout=1.5)
        self._thread = None
        self._socket = None
        self._connected_event.clear()
        self.connection_status_changed.emit("Disconnected")

    def _thread_main(self) -> None:
        try:
            from websockets.sync.client import connect

            with connect(
                self.websocket_uri,
                open_timeout=5,
                close_timeout=2,
                max_size=8 * 1024 * 1024,
            ) as ws:
                self._socket = ws
                self._connected_event.set()
                self.connection_status_changed.emit(f"Connected to {self._host}:{self._port}")

                # Worker immediately sends STATUS after WebSocket accept.
                try:
                    raw = ws.recv(timeout=3.0)
                    self._handle_raw(raw)
                except TimeoutError:
                    pass

                while not self._stop_event.is_set():
                    while True:
                        try:
                            payload = self._send_q.get_nowait()
                        except queue.Empty:
                            break
                        ws.send(json.dumps(payload))

                    try:
                        raw = ws.recv(timeout=0.10)
                    except TimeoutError:
                        continue
                    self._handle_raw(raw)
        except Exception as exc:
            if not self._stop_event.is_set():
                self._connection_error = str(exc)
                logger.exception("Ubuntu worker connection failed")
                self.remote_error.emit(str(exc))
                self.connection_status_changed.emit(f"Connection error: {exc}")
                self._fail_all_waiters(str(exc))
        finally:
            self._socket = None
            self._connected_event.clear()
            if not self._stop_event.is_set():
                self.connection_status_changed.emit("Disconnected")

    # ------------------------------------------------------------------
    def _send_and_wait(
        self,
        payload: dict[str, Any],
        expected: Iterable[str],
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        if not self.connected:
            raise RuntimeError("Ubuntu worker is not connected")
        waiter = _Waiter({str(x).upper() for x in expected}, threading.Event())
        with self._waiter_lock:
            self._waiters.append(waiter)
        self._send_q.put(dict(payload))
        if not waiter.event.wait(timeout):
            with self._waiter_lock:
                if waiter in self._waiters:
                    self._waiters.remove(waiter)
            raise TimeoutError(
                f"Ubuntu worker did not respond to {payload.get('type')} within {timeout:.1f}s"
            )
        if waiter.error:
            raise RuntimeError(waiter.error)
        return dict(waiter.response or {})

    def _handle_raw(self, raw: str | bytes) -> None:
        try:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="replace")
            msg = json.loads(raw)
            if not isinstance(msg, dict):
                return
        except Exception:
            logger.warning("Ignoring non-JSON Ubuntu worker message")
            return

        msg = dict(msg)
        msg["console_receive_timestamp_utc_ns"] = time.time_ns()
        msg["console_receive_monotonic_ns"] = time.monotonic_ns()
        kind = str(msg.get("type", "")).upper()

        if kind == "STATUS":
            self._status = dict(msg)
        self._write_received_message(msg)

        matched = False
        with self._waiter_lock:
            for waiter in list(self._waiters):
                if kind == "ERROR" or kind in waiter.expected:
                    self._waiters.remove(waiter)
                    if kind == "ERROR":
                        waiter.error = str(msg.get("message") or "Ubuntu worker returned ERROR")
                    else:
                        waiter.response = dict(msg)
                    waiter.event.set()
                    matched = True
                    break
        _ = matched

        self.message_received.emit(msg)
        if kind == "TRIAL_PREPARED":
            self.trial_prepared.emit(msg)
        elif kind == "TASK_STARTED":
            self.task_started.emit(msg)
        elif kind in {"TASK_ENDED", "TASK_STOPPED", "TASK_ALREADY_STOPPED"}:
            self.task_ended.emit(msg)
        elif kind == "RL_PROCESS_FAILED":
            stderr_tail = msg.get("stderr_tail") or []
            if isinstance(stderr_tail, list):
                detail = "\n".join(str(x) for x in stderr_tail[-25:])
            else:
                detail = str(stderr_tail)
            text = (
                f"Ubuntu GA3C process failed with return code {msg.get('returncode')}."
                + (f"\n\n{detail}" if detail else "")
            )
            self.remote_error.emit(text)
        elif kind == "STATE_UPDATE":
            self.state_updated.emit(msg)
        elif kind == "COLLISION":
            self.collision.emit(msg)
        elif kind == "HUMAN_ACTION_REQUEST":
            self.human_action_requested.emit(msg)
        elif kind == "HUMAN_ACTION_APPLIED":
            self.human_action_applied.emit(msg)
        elif kind == "EPISODE_STARTED":
            self.episode_started.emit(msg)
        elif kind == "EPISODE_ENDED":
            self.episode_ended.emit(msg)
        elif kind == "ERROR":
            self.remote_error.emit(str(msg.get("message") or "Unknown Ubuntu worker error"))

    def _fail_all_waiters(self, error: str) -> None:
        with self._waiter_lock:
            waiters = list(self._waiters)
            self._waiters.clear()
        for waiter in waiters:
            waiter.error = error
            waiter.event.set()

    # ------------------------------------------------------------------
    def get_status(self, timeout: float = 4.0) -> dict[str, Any]:
        return self._send_and_wait({"type": "GET_STATUS"}, {"STATUS"}, timeout=timeout)

    def measure_clock_offset(self, samples: int = 5, timeout: float = 2.0) -> dict[str, Any]:
        samples = max(1, int(samples))
        offsets: list[float] = []
        rtts: list[float] = []
        records: list[dict[str, Any]] = []
        for index in range(samples):
            request_id = f"clock-{time.time_ns()}-{index}"
            sent = time.time_ns()
            response = self._send_and_wait(
                {"type": "PING", "request_id": request_id, "timestamp_utc_ns": sent},
                {"PONG"},
                timeout=timeout,
            )
            received = time.time_ns()
            worker = int(response.get("worker_timestamp_utc_ns") or 0)
            rtt_ns = received - sent
            offset_ns = worker - ((sent + received) // 2)
            offsets.append(float(offset_ns))
            rtts.append(float(rtt_ns))
            records.append(
                {
                    "request_id": request_id,
                    "send_timestamp_utc_ns": sent,
                    "receive_timestamp_utc_ns": received,
                    "worker_timestamp_utc_ns": worker,
                    "rtt_ns": rtt_ns,
                    "estimated_worker_minus_console_offset_ns": offset_ns,
                }
            )
        self._clock_sync = {
            "samples": records,
            "median_rtt_ns": int(statistics.median(rtts)),
            "median_worker_minus_console_offset_ns": int(statistics.median(offsets)),
            "measured_at_utc_ns": time.time_ns(),
        }
        return self.clock_sync

    # ------------------------------------------------------------------
    def set_active_trial(self, trial) -> None:
        self._active_trial_id = trial.trial_id
        self._trial_path = Path(trial.trial_dir) if trial.trial_dir else None
        if self._trial_path is None:
            return
        self._remote_event_path = self._trial_path / "events" / "ubuntu_remote_events.jsonl"
        self._state_csv_path = self._trial_path / "rl" / "continuous_nav_state_stream.csv"
        self._action_csv_path = self._trial_path / "input" / "continuous_nav_actions.csv"
        for path in (self._remote_event_path, self._state_csv_path, self._action_csv_path):
            path.parent.mkdir(parents=True, exist_ok=True)

    def clear_active_trial(self) -> None:
        self._active_trial_id = None
        self._trial_path = None
        self._remote_event_path = None
        self._state_csv_path = None
        self._action_csv_path = None

    def _write_received_message(self, msg: dict[str, Any]) -> None:
        if not self._active_trial_id:
            return
        trial_id = msg.get("trial_id")
        if trial_id and str(trial_id) != self._active_trial_id:
            return
        with self._log_lock:
            if self._remote_event_path is not None:
                with self._remote_event_path.open("a", encoding="utf-8") as fp:
                    fp.write(json.dumps(msg, separators=(",", ":"), default=str) + "\n")
            if str(msg.get("type", "")).upper() == "STATE_UPDATE" and self._state_csv_path is not None:
                fields = [
                    "console_receive_timestamp_utc_ns",
                    "timestamp_utc_ns",
                    "monotonic_ns",
                    "episode",
                    "step",
                    "phase",
                    "robot_x",
                    "robot_y",
                    "robot_orientation",
                    "goal_x",
                    "goal_y",
                    "goal_radius",
                    "action_source",
                    "action",
                    "reward",
                    "done",
                    "collision",
                    "intervention_id",
                    "human_step",
                    "human_total_steps",
                ]
                exists = self._state_csv_path.exists() and self._state_csv_path.stat().st_size > 0
                with self._state_csv_path.open("a", newline="", encoding="utf-8") as fp:
                    writer = csv.DictWriter(fp, fieldnames=fields, extrasaction="ignore")
                    if not exists:
                        writer.writeheader()
                    writer.writerow({key: msg.get(key, "") for key in fields})

    def _write_action(
        self,
        *,
        request_id: str,
        action: int | None,
        modality: str,
        source_detail: str,
        timestamp_utc_ns: int,
    ) -> None:
        if self._action_csv_path is None:
            return
        fields = [
            "timestamp_utc_ns",
            "monotonic_ns",
            "trial_id",
            "request_id",
            "action",
            "modality",
            "source_detail",
        ]
        row = {
            "timestamp_utc_ns": timestamp_utc_ns,
            "monotonic_ns": time.monotonic_ns(),
            "trial_id": self._active_trial_id or "",
            "request_id": request_id,
            "action": "" if action is None else int(action),
            "modality": modality,
            "source_detail": source_detail,
        }
        with self._log_lock:
            exists = self._action_csv_path.exists() and self._action_csv_path.stat().st_size > 0
            with self._action_csv_path.open("a", newline="", encoding="utf-8") as fp:
                writer = csv.DictWriter(fp, fieldnames=fields)
                if not exists:
                    writer.writeheader()
                writer.writerow(row)

    # ------------------------------------------------------------------
    def prepare_trial(
        self,
        trial,
        *,
        hil_correction_length: int,
        feedback_timeout_seconds: float,
        agents: int = 1,
        predictors: int = 1,
        trainers: int = 1,
        visualize_on_ubuntu: bool = False,
        timeout: float = 12.0,
    ) -> dict[str, Any]:
        self.set_active_trial(trial)
        payload = {
            "type": "PREPARE_TRIAL",
            "trial": {
                "participant_id": trial.participant_code,
                "session_id": trial.session_id,
                "study_id": "Study1B_ContinuousNavigation",
                "trial_id": trial.trial_id,
                "run_id": trial.run_code or "R01",
                "feedback_mode": trial.condition.feedback_timing.value,
                "modality": trial.condition.modality.value,
                "hil_correction_length": int(hil_correction_length),
                "feedback_timeout_seconds": float(feedback_timeout_seconds),
                "agents": int(agents),
                "predictors": int(predictors),
                "trainers": int(trainers),
                "visualize_on_ubuntu": bool(visualize_on_ubuntu),
                "console_clock_sync": self.clock_sync,
            },
        }
        return self._send_and_wait(payload, {"TRIAL_PREPARED"}, timeout=timeout)

    def start_trial(self, timeout: float = 12.0) -> dict[str, Any]:
        return self._send_and_wait({"type": "START_TRIAL"}, {"TASK_STARTED"}, timeout=timeout)

    def send_action(
        self,
        request_id: str,
        action: int | None,
        *,
        modality: str,
        source_detail: str,
    ) -> int:
        if not self.connected:
            raise RuntimeError("Ubuntu worker is not connected")
        request_id = str(request_id or "")
        if not request_id:
            raise ValueError("Human-action request_id is missing")
        timestamp_utc_ns = time.time_ns()
        self._write_action(
            request_id=request_id,
            action=action,
            modality=modality,
            source_detail=source_detail,
            timestamp_utc_ns=timestamp_utc_ns,
        )
        self._send_q.put(
            {
                "type": "ACTION",
                "request_id": request_id,
                "action": None if action is None else int(action),
                "timestamp_utc_ns": timestamp_utc_ns,
            }
        )
        return timestamp_utc_ns

    def stop_trial(self, *, aborted: bool = False, reason: str = "operator_stop", timeout: float = 15.0) -> dict[str, Any]:
        kind = "ABORT_TRIAL" if aborted else "STOP_TRIAL"
        return self._send_and_wait(
            {"type": kind, "reason": reason},
            {"TASK_STOPPED", "TASK_ALREADY_STOPPED", "TASK_ENDED"},
            timeout=timeout,
        )

    def finalize_trial(self, timeout: float = 10.0) -> dict[str, Any]:
        return self._send_and_wait({"type": "FINALIZE_TRIAL"}, {"TRIAL_FINALIZED"}, timeout=timeout)

    def download_trial_bundle(self, destination_dir: Path, timeout: float = 30.0) -> Path:
        destination_dir = Path(destination_dir)
        destination_dir.mkdir(parents=True, exist_ok=True)
        response = requests.get(f"{self.http_base}/trial/bundle", timeout=timeout)
        response.raise_for_status()
        bundle_path = destination_dir / "ubuntu_trial_bundle.zip"
        bundle_path.write_bytes(response.content)
        extract_dir = destination_dir / "extracted"
        extract_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(bundle_path, "r") as zf:
            zf.extractall(extract_dir)
        self._verify_extracted_checksums(extract_dir)
        return bundle_path

    @staticmethod
    def _verify_extracted_checksums(extract_dir: Path) -> None:
        checksum_path = extract_dir / "checksums.json"
        if not checksum_path.exists():
            return
        try:
            import hashlib

            payload = json.loads(checksum_path.read_text(encoding="utf-8"))
            files = payload.get("files", {})
            if not isinstance(files, dict):
                return
            failures = []
            for rel, expected in files.items():
                # Ubuntu worker v1 updates manifest.json after calculating the
                # checksum table, so that one entry is intentionally stale.
                # Verify every immutable data file while tolerating this known
                # v1 manifest-finalization ordering issue.
                if str(rel).replace("\\", "/") == "manifest.json":
                    continue
                path = extract_dir / rel
                if not path.exists():
                    failures.append(f"missing:{rel}")
                    continue
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                if digest.lower() != str(expected).lower():
                    failures.append(f"hash:{rel}")
            if failures:
                raise RuntimeError("Ubuntu bundle checksum verification failed: " + ", ".join(failures[:10]))
        except Exception:
            logger.exception("Ubuntu trial bundle checksum verification failed")
            raise
