"""Microsoft HoloLens 2 live-stream adapter using the HL2SS Python client.

The console intentionally does not vendor HL2SS.  The experimenter downloads the
HL2SS repository/app from its upstream project, installs the server app on the
headset, and points this adapter at the repository's ``viewer`` directory.

This adapter keeps two independent streams open after connection:

* Personal Video (PV/front RGB camera), decoded to BGR frames.
* Extended Eye Tracking (EET), including combined/left/right gaze rays.

The GUI validation window is only a viewer over these live streams; closing the
window does not stop acquisition.
"""

from __future__ import annotations

import csv
import importlib
import json
import logging
import socket
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from collections import deque
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from PySide6.QtCore import Signal

from devices.base_device import BaseDevice
from models.enums import DeviceStatus, DeviceType
from models.trial import Trial

logger = logging.getLogger(__name__)


@dataclass
class _HoloLensTrialRecording:
    trial_id: str
    participant_code: str
    session_id: str
    condition_code: str
    run_code: str
    condition_name: str
    study: str
    environment: str
    feedback_timing: str
    modality: str
    practice: bool
    trial_started_at: float
    recording_started_at: float
    recording_dir: Path
    video_path: Path
    pointer_csv_path: Path
    eet_csv_path: Path
    metadata_path: Path
    video_writer: Any
    pointer_handle: Any
    pointer_writer: Any
    eet_handle: Any
    eet_writer: Any
    video_frame_count: int = 0
    pointer_row_count: int = 0
    eet_row_count: int = 0
    last_flush_monotonic: float = 0.0
    smoothed_gaze: tuple[float, float] | None = None


class HoloLensDevice(BaseDevice):
    """Live HoloLens 2 PV-camera + Extended Eye Tracking connection."""

    connection_progress = Signal(int, str)
    log_message = Signal(str)
    stream_stats_changed = Signal(object)

    DEFAULT_WIDTH = 1280
    DEFAULT_HEIGHT = 720
    DEFAULT_CAMERA_FPS = 30
    DEFAULT_EYE_FPS = 60
    PORT_PV = 3810
    PORT_EET = 3817

    def __init__(self, parent=None) -> None:
        super().__init__(DeviceType.HOLOLENS, parent)
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._threads: list[threading.Thread] = []
        self._pv_client = None
        self._eet_client = None
        self._hl2ss = None
        self._hl2ss_lnm = None
        self._pv_subsystem_started = False

        self._host = ""
        self._client_dir = ""
        self._width = self.DEFAULT_WIDTH
        self._height = self.DEFAULT_HEIGHT
        self._camera_fps = self.DEFAULT_CAMERA_FPS
        self._eye_fps = self.DEFAULT_EYE_FPS

        self._connect_started_monotonic: float | None = None
        self._camera_started_monotonic: float | None = None
        self._eye_started_monotonic: float | None = None
        self._last_camera_monotonic: float | None = None
        self._last_eye_monotonic: float | None = None
        self._camera_frame_count = 0
        self._eye_packet_count = 0
        self._latest_camera_frame = None
        self._latest_camera_timestamp = None
        self._latest_camera_meta: dict[str, Any] = {}
        self._latest_eye: dict[str, Any] = {}
        self._eye_history: deque[dict[str, Any]] = deque(maxlen=360)
        self._last_error = ""
        self._camera_ready = False
        self._eye_ready = False
        self._last_stats_emit = 0.0

        # Trial-scoped study recording. The live HL2SS streams remain open
        # continuously; these sinks are opened only while a Training/Study R##
        # run is active, so reconnecting the headset is not required between runs.
        self._recording_lock = threading.RLock()
        self._trial_recording: _HoloLensTrialRecording | None = None
        self._gaze_projection_distance_m = 1.5

    # ------------------------------------------------------------------
    # Configuration / dependency discovery

    @staticmethod
    def resolve_client_dir(path: str | Path | None) -> Path | None:
        """Return the HL2SS ``viewer`` directory represented by *path*.

        Accepted selections are either the repository root or the viewer folder.
        """
        if not path:
            return None
        candidate = Path(path).expanduser()
        options = (candidate, candidate / "viewer")
        for option in options:
            if (option / "hl2ss.py").is_file() and (option / "hl2ss_lnm.py").is_file():
                return option.resolve()
        return None

    @classmethod
    def client_dir_valid(cls, path: str | Path | None) -> bool:
        return cls.resolve_client_dir(path) is not None

    def configure(
        self,
        host: str,
        client_dir: str | Path,
        *,
        eye_fps: int = DEFAULT_EYE_FPS,
        width: int = DEFAULT_WIDTH,
        height: int = DEFAULT_HEIGHT,
        camera_fps: int = DEFAULT_CAMERA_FPS,
    ) -> None:
        host = str(host).strip()
        if not host:
            raise ValueError("Enter the HoloLens IPv4 address before connecting.")
        try:
            socket.inet_aton(host)
        except OSError as exc:
            raise ValueError(f"'{host}' is not a valid IPv4 address.") from exc

        resolved = self.resolve_client_dir(client_dir)
        if resolved is None:
            raise ValueError(
                "The selected HL2SS folder is invalid. Select either the cloned/downloaded "
                "HL2SS repository root or its 'viewer' folder; it must contain hl2ss.py "
                "and hl2ss_lnm.py."
            )
        if int(eye_fps) not in (30, 60, 90):
            raise ValueError("Extended Eye Tracking frame rate must be 30, 60, or 90 Hz.")

        with self._lock:
            self._host = host
            self._client_dir = str(resolved)
            self._eye_fps = int(eye_fps)
            self._width = int(width)
            self._height = int(height)
            self._camera_fps = int(camera_fps)

    # ------------------------------------------------------------------
    # BaseDevice implementation

    def connect_device(self) -> None:
        if self.status in (
            DeviceStatus.CONNECTING,
            DeviceStatus.CONNECTED,
            DeviceStatus.RECEIVING_DATA,
        ):
            return
        if not self._host or not self._client_dir:
            raise RuntimeError(
                "HoloLens connection settings are incomplete. Use connect_hololens(host, client_dir, ...) "
                "from the Devices page."
            )

        self._reset_runtime_state()
        self._set_status(DeviceStatus.CONNECTING)
        self._progress(5, "Loading the HL2SS Python client...")
        thread = threading.Thread(
            target=self._connect_worker,
            name="HINT-HoloLens-Connect",
            daemon=True,
        )
        self._threads = [thread]
        thread.start()

    def disconnect_device(self) -> None:
        # Finalize files first so an unplug/disconnect does not leave an MP4 or
        # CSV handle open. The controller will receive None if it later tries to
        # stop the already-finalized trial recording.
        try:
            self.stop_trial_recording(reason="hololens_disconnected")
        except Exception:
            logger.exception("Could not finalize HoloLens trial recording during disconnect")

        self._stop_event.set()
        self._progress(0, "Disconnecting HoloLens streams...")

        # Closing the sockets from this thread unblocks get_next_packet().
        for client in (self._pv_client, self._eet_client):
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass

        if self._pv_subsystem_started and self._hl2ss_lnm is not None and self._hl2ss is not None:
            try:
                self._hl2ss_lnm.stop_subsystem_pv(
                    self._host,
                    self._hl2ss.StreamPort.PERSONAL_VIDEO,
                    sockopt=self._hl2ss_lnm.create_sockopt(settimeout=2.0),
                )
            except Exception:
                logger.debug("PV subsystem stop failed during disconnect", exc_info=True)

        with self._lock:
            self._pv_client = None
            self._eet_client = None
            self._pv_subsystem_started = False
            self._camera_ready = False
            self._eye_ready = False
        self._set_status(DeviceStatus.DISCONNECTED)
        self._log("HoloLens disconnected.")
        self._emit_stats(force=True)

    # ------------------------------------------------------------------

    def _reset_runtime_state(self) -> None:
        self._stop_event.clear()
        with self._lock:
            self._connect_started_monotonic = time.monotonic()
            self._camera_started_monotonic = None
            self._eye_started_monotonic = None
            self._last_camera_monotonic = None
            self._last_eye_monotonic = None
            self._camera_frame_count = 0
            self._eye_packet_count = 0
            self._latest_camera_frame = None
            self._latest_camera_timestamp = None
            self._latest_camera_meta = {}
            self._latest_eye = {}
            self._eye_history.clear()
            self._last_error = ""
            self._camera_ready = False
            self._eye_ready = False
            self._last_stats_emit = 0.0
            self._pv_client = None
            self._eet_client = None
            self._pv_subsystem_started = False

    def _connect_worker(self) -> None:
        try:
            self._load_hl2ss_modules()
            self._progress(25, "HL2SS client loaded. Opening camera and eye-gaze streams on the HoloLens...")
            if self._stop_event.is_set():
                return

            pv_thread = threading.Thread(
                target=self._pv_worker,
                name="HINT-HoloLens-PV",
                daemon=True,
            )
            eye_thread = threading.Thread(
                target=self._eye_worker,
                name="HINT-HoloLens-EET",
                daemon=True,
            )
            self._threads.extend([pv_thread, eye_thread])
            pv_thread.start()
            eye_thread.start()
            self._log(
                f"Opened HoloLens stream workers for {self._host}: "
                f"PV {self._width}x{self._height}@{self._camera_fps}, EET {self._eye_fps} Hz."
            )
        except Exception as exc:
            if not self._stop_event.is_set():
                self._fail_connection(f"Could not connect to HoloLens: {exc}")

    def _load_hl2ss_modules(self) -> None:
        client_dir = str(self._client_dir)
        if client_dir not in sys.path:
            sys.path.insert(0, client_dir)
        try:
            self._hl2ss = importlib.import_module("hl2ss")
            self._hl2ss_lnm = importlib.import_module("hl2ss_lnm")
        except Exception as exc:
            raise RuntimeError(
                "HL2SS Python client could not be imported. Ensure the selected folder is the "
                "HL2SS viewer folder and install this console's updated requirements "
                "(`pip install -r requirements.txt`). Original import error: " + str(exc)
            ) from exc

    def _pv_worker(self) -> None:
        hl2ss = self._hl2ss
        lnm = self._hl2ss_lnm
        if hl2ss is None or lnm is None:
            return
        try:
            sockopt = lnm.create_sockopt(settimeout=3.0)
            lnm.start_subsystem_pv(
                self._host,
                hl2ss.StreamPort.PERSONAL_VIDEO,
                sockopt=sockopt,
                enable_mrc=False,
                shared=False,
            )
            self._pv_subsystem_started = True
            self._progress(45, "PV camera subsystem started; waiting for the first RGB frame...")
            client = lnm.rx_pv(
                self._host,
                hl2ss.StreamPort.PERSONAL_VIDEO,
                sockopt=sockopt,
                mode=hl2ss.StreamMode.MODE_1,
                width=self._width,
                height=self._height,
                framerate=self._camera_fps,
                profile=hl2ss.VideoProfile.H264_MAIN,
                bitrate=None,
                decoded_format="bgr24",
            )
            self._pv_client = client
            client.open()
            with self._lock:
                self._camera_started_monotonic = time.monotonic()
            while not self._stop_event.is_set():
                packet = client.get_next_packet()
                now = time.monotonic()
                frame = packet.payload.image
                timestamp = getattr(packet, "timestamp", None)
                camera_meta = {
                    "timestamp": timestamp,
                    "pose": self._matrix4(getattr(packet, "pose", None)),
                    "focal_length": self._vector2(getattr(packet.payload, "focal_length", None)),
                    "principal_point": self._vector2(getattr(packet.payload, "principal_point", None)),
                    "width": int(frame.shape[1]) if getattr(frame, "ndim", 0) >= 2 else self._width,
                    "height": int(frame.shape[0]) if getattr(frame, "ndim", 0) >= 2 else self._height,
                }
                with self._lock:
                    self._latest_camera_frame = frame
                    self._latest_camera_timestamp = timestamp
                    self._latest_camera_meta = camera_meta
                    self._last_camera_monotonic = now
                    self._camera_frame_count += 1
                    first = not self._camera_ready
                    self._camera_ready = True
                try:
                    self._record_pv_frame(frame, camera_meta, host_monotonic=now)
                except Exception as exc:
                    logger.exception("HoloLens PV trial recording failed")
                    self._log(f"HoloLens trial recording stopped after PV write error: {exc}")
                    try:
                        self.stop_trial_recording(reason="pv_recording_error")
                    except Exception:
                        logger.exception("Could not finalize failed HoloLens PV recording")
                if first:
                    self._log("First HoloLens PV camera frame received.")
                    self._progress(65, "PV camera verified; waiting for live eye-gaze data...")
                    self._update_connected_status()
                self._emit_stats()
        except Exception as exc:
            self._handle_stream_error("PV camera", exc)
        finally:
            client = self._pv_client
            self._pv_client = None
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass

    def _eye_worker(self) -> None:
        hl2ss = self._hl2ss
        lnm = self._hl2ss_lnm
        if hl2ss is None or lnm is None:
            return
        try:
            self._progress(50, "Opening Extended Eye Tracking stream...")
            # IMPORTANT: Do not use a short socket read timeout for EET.
            # The official HL2SS client_stream_eet.py uses the default blocking
            # socket. On first use, HoloLens may need time to resolve eye-tracker
            # permission/calibration and initialize the Extended Eye Tracking
            # provider. A 3 s read timeout can therefore report a false failure.
            sockopt = lnm.create_sockopt()
            client = lnm.rx_eet(
                self._host,
                hl2ss.StreamPort.EXTENDED_EYE_TRACKER,
                sockopt=sockopt,
                fps=self._eye_fps,
            )
            self._eet_client = client
            client.open()
            with self._lock:
                self._eye_started_monotonic = time.monotonic()
            while not self._stop_event.is_set():
                packet = client.get_next_packet()
                now = time.monotonic()
                eye = self.extract_eye_packet(packet)
                with self._lock:
                    self._latest_eye = eye
                    self._eye_history.append(eye)
                    self._last_eye_monotonic = now
                    self._eye_packet_count += 1
                    first = not self._eye_ready
                    self._eye_ready = True
                try:
                    self._record_eet_sample(eye, host_monotonic=now)
                except Exception as exc:
                    logger.exception("HoloLens EET trial recording failed")
                    self._log(f"HoloLens trial recording stopped after EET write error: {exc}")
                    try:
                        self.stop_trial_recording(reason="eet_recording_error")
                    except Exception:
                        logger.exception("Could not finalize failed HoloLens EET recording")
                if first:
                    self._log("First HoloLens Extended Eye Tracking packet received.")
                    if not eye.get("calibration_valid", False):
                        self._log(
                            "Eye-gaze stream is live, but the current HoloLens eye calibration is not valid. "
                            "Run eye calibration on the headset before study collection."
                        )
                    self._update_connected_status()
                self._emit_stats()
        except Exception as exc:
            self._handle_stream_error("Extended Eye Tracking", exc)
        finally:
            client = self._eet_client
            self._eet_client = None
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass

    @staticmethod
    def _vector2(value: Any) -> tuple[float, float] | None:
        if value is None:
            return None
        try:
            if hasattr(value, "x"):
                return (float(value.x), float(value.y))
            if hasattr(value, "__len__") and len(value) >= 2:
                return (float(value[0]), float(value[1]))
        except Exception:
            return None
        return None

    @staticmethod
    def _matrix4(value: Any):
        if value is None:
            return None
        try:
            matrix = np.asarray(value, dtype=np.float64).reshape((4, 4))
            if not np.all(np.isfinite(matrix)):
                return None
            return matrix.copy()
        except Exception:
            return None

    @staticmethod
    def _vector3(value: Any) -> tuple[float, float, float] | None:
        if value is None:
            return None
        try:
            if hasattr(value, "x"):
                return (float(value.x), float(value.y), float(value.z))
            if hasattr(value, "__len__") and len(value) >= 3:
                return (float(value[0]), float(value[1]), float(value[2]))
        except Exception:
            return None
        return None

    @classmethod
    def _ray(cls, ray: Any) -> dict[str, Any]:
        return {
            "origin": cls._vector3(getattr(ray, "origin", None)),
            "direction": cls._vector3(getattr(ray, "direction", None)),
        }

    @classmethod
    def extract_eye_packet(cls, packet: Any) -> dict[str, Any]:
        eet = packet.payload
        return {
            "timestamp": getattr(packet, "timestamp", None),
            # EET gaze rays are expressed in the eye tracker's coordinate system.
            # packet.pose registers that tracker coordinate system to the world.
            "pose": cls._matrix4(getattr(packet, "pose", None)),
            "calibration_valid": bool(getattr(eet, "calibration_valid", False)),
            "combined_valid": bool(getattr(eet, "combined_ray_valid", False)),
            "combined": cls._ray(getattr(eet, "combined_ray", None)),
            "left_valid": bool(getattr(eet, "left_ray_valid", False)),
            "left": cls._ray(getattr(eet, "left_ray", None)),
            "right_valid": bool(getattr(eet, "right_ray_valid", False)),
            "right": cls._ray(getattr(eet, "right_ray", None)),
            # Retained for completeness. Microsoft documents these as unsupported
            # by HoloLens 2 at present, so the validation UI labels them accordingly.
            "left_openness_valid": bool(getattr(eet, "left_openness_valid", False)),
            "left_openness": getattr(eet, "left_openness", None),
            "right_openness_valid": bool(getattr(eet, "right_openness_valid", False)),
            "right_openness": getattr(eet, "right_openness", None),
            "vergence_distance_valid": bool(getattr(eet, "vergence_distance_valid", False)),
            "vergence_distance": getattr(eet, "vergence_distance", None),
        }

    @staticmethod
    def _transform_point(point: np.ndarray, transform: np.ndarray) -> np.ndarray:
        """Apply an HL2SS row-vector 4x4 transform to one XYZ point."""
        return point @ transform[:3, :3] + transform[3, :3]

    @classmethod
    def project_gaze_to_pv(
        cls,
        eye: dict[str, Any],
        camera: dict[str, Any],
        *,
        distance_m: float = 1.5,
    ) -> dict[str, Any]:
        """Project the combined EET gaze direction into a PV image.

        The eye tracker does not provide a scene intersection distance on HoloLens 2.
        For validation, we therefore project a point ``distance_m`` metres along the
        combined gaze ray (1.5 m by default, matching Microsoft's gaze-visualization
        sample). This produces a pose-registered *gaze-direction* cursor, not a
        depth-resolved fixation point on a physical surface.
        """
        result: dict[str, Any] = {
            "valid": False,
            "in_frame": False,
            "pixel": None,
            "distance_m": float(distance_m),
            "timestamp_delta_ms": None,
            "reason": "Gaze projection unavailable.",
        }

        if not eye or not camera:
            result["reason"] = "Waiting for synchronized PV and eye-gaze data."
            return result
        if not bool(eye.get("calibration_valid", False)):
            result["reason"] = "Eye calibration is not valid."
            return result
        if not bool(eye.get("combined_valid", False)):
            result["reason"] = "Combined eye-gaze ray is not valid."
            return result

        ray = eye.get("combined") or {}
        origin = ray.get("origin")
        direction = ray.get("direction")
        eye_pose = eye.get("pose")
        pv_pose = camera.get("pose")
        focal = camera.get("focal_length")
        principal = camera.get("principal_point")
        if origin is None or direction is None or eye_pose is None or pv_pose is None:
            result["reason"] = "A valid EET/PV pose has not arrived yet."
            return result
        if focal is None or principal is None:
            result["reason"] = "PV intrinsics have not arrived yet."
            return result

        try:
            o = np.asarray(origin, dtype=np.float64).reshape(3)
            d = np.asarray(direction, dtype=np.float64).reshape(3)
            eye_pose = np.asarray(eye_pose, dtype=np.float64).reshape((4, 4))
            pv_pose = np.asarray(pv_pose, dtype=np.float64).reshape((4, 4))
            norm = float(np.linalg.norm(d))
            if (not np.isfinite(norm)) or norm < 1e-9:
                result["reason"] = "Combined eye-gaze direction has zero length."
                return result
            d /= norm

            # Eye tracker coordinates -> world coordinates.
            tracker_point = o + float(distance_m) * d
            world_point = cls._transform_point(tracker_point, eye_pose)

            # World -> PV reference coordinates. MODE_1 PV packets carry a pose.
            pv_reference = cls._transform_point(world_point, np.linalg.inv(pv_pose))

            # Match hl2ss_3dcv.pv_fix_calibration(): PV camera convention flips Y/Z.
            pv_camera = np.array(
                [pv_reference[0], -pv_reference[1], -pv_reference[2]],
                dtype=np.float64,
            )
            z = float(pv_camera[2])
            if (not np.isfinite(z)) or z <= 1e-6:
                result["reason"] = "Projected gaze is behind the PV camera."
                return result

            fx, fy = float(focal[0]), float(focal[1])
            cx, cy = float(principal[0]), float(principal[1])
            u = fx * float(pv_camera[0]) / z + cx
            v = fy * float(pv_camera[1]) / z + cy
            if not (np.isfinite(u) and np.isfinite(v)):
                result["reason"] = "Projected gaze pixel is not finite."
                return result

            width = int(camera.get("width") or 0)
            height = int(camera.get("height") or 0)
            in_frame = 0.0 <= u < width and 0.0 <= v < height
            result.update(
                {
                    "valid": True,
                    "in_frame": bool(in_frame),
                    "pixel": (float(u), float(v)),
                    "reason": "Gaze direction visible." if in_frame else "Gaze direction is outside the PV image.",
                }
            )

            eye_ts = eye.get("timestamp")
            pv_ts = camera.get("timestamp")
            if eye_ts is not None and pv_ts is not None:
                # HL2SS sensor timestamps use 100 ns ticks: 10,000 ticks per ms.
                result["timestamp_delta_ms"] = abs(float(eye_ts) - float(pv_ts)) / 10000.0
            return result
        except Exception as exc:
            result["reason"] = f"Gaze projection failed: {exc}"
            return result

    @staticmethod
    def _nearest_eye_sample(eye_history: list[dict[str, Any]], timestamp: Any) -> dict[str, Any] | None:
        if timestamp is None or not eye_history:
            return eye_history[-1] if eye_history else None
        candidates = [sample for sample in eye_history if sample.get("timestamp") is not None]
        if not candidates:
            return eye_history[-1]
        return min(candidates, key=lambda sample: abs(float(sample["timestamp"]) - float(timestamp)))

    def latest_camera_gaze_snapshot(self, distance_m: float = 1.5) -> dict[str, Any]:
        """Return the latest PV frame plus the nearest-in-time projected gaze cursor."""
        with self._lock:
            frame = self._latest_camera_frame
            camera = dict(self._latest_camera_meta)
            eye_history = list(self._eye_history)
        eye = self._nearest_eye_sample(eye_history, camera.get("timestamp"))
        overlay = self.project_gaze_to_pv(eye or {}, camera, distance_m=distance_m)
        return {"frame": frame, "camera": camera, "eye": eye or {}, "gaze_overlay": overlay}

    # ------------------------------------------------------------------
    # Trial-scoped HoloLens recording

    @staticmethod
    def _iso_utc(epoch_s: float) -> str:
        return datetime.fromtimestamp(epoch_s, tz=timezone.utc).isoformat()

    @staticmethod
    def _ray_components(eye: dict[str, Any], key: str) -> tuple[Any, Any, Any, Any, Any, Any]:
        ray = eye.get(key) or {}
        origin = ray.get("origin") or (None, None, None)
        direction = ray.get("direction") or (None, None, None)
        return (*origin, *direction)

    def is_stream_healthy(self, max_age_s: float = 1.0) -> bool:
        stats = self.stats()
        camera_age = stats.get("last_camera_age_s")
        eye_age = stats.get("last_eye_age_s")
        return bool(
            stats.get("camera_ready")
            and stats.get("eye_ready")
            and camera_age is not None
            and eye_age is not None
            and float(camera_age) <= float(max_age_s)
            and float(eye_age) <= float(max_age_s)
        )

    def start_trial_recording(self, trial: Trial) -> dict[str, Path]:
        """Record annotated PV video + synchronized gaze data for one R## run.

        Unlike Shimmer, HoloLens recording is intentionally enabled for both
        Training (practice=True) and primary Study trials. All files remain
        underneath the already-allocated readable trial directory.
        """
        if trial.trial_path is None:
            raise ValueError("Trial has no storage directory")
        if trial.started_at is None:
            raise ValueError("Trial must be started before HoloLens recording begins")
        if not self.is_stream_healthy():
            raise RuntimeError(
                "HoloLens PV/EET streams are not currently fresh. Use Devices -> "
                "Validate Connection before starting the activity."
            )

        recording_dir = trial.trial_path / "sensors" / "hololens"
        recording_dir.mkdir(parents=True, exist_ok=True)
        video_path = recording_dir / "hololens_pv_gaze_overlay.mp4"
        pointer_csv_path = recording_dir / "hololens_gaze_pointer.csv"
        eet_csv_path = recording_dir / "hololens_eet_raw.csv"
        metadata_path = recording_dir / "hololens_recording_metadata.json"
        started = time.time()

        with self._recording_lock:
            if self._trial_recording is not None:
                if self._trial_recording.trial_id == trial.trial_id:
                    return {
                        "video": self._trial_recording.video_path,
                        "pointer_csv": self._trial_recording.pointer_csv_path,
                        "eet_csv": self._trial_recording.eet_csv_path,
                        "metadata": self._trial_recording.metadata_path,
                    }
                raise RuntimeError(
                    f"HoloLens is already recording trial {self._trial_recording.trial_id}"
                )

            pointer_handle = open(pointer_csv_path, "w", newline="", encoding="utf-8")
            eet_handle = open(eet_csv_path, "w", newline="", encoding="utf-8")
            pointer_writer = csv.writer(pointer_handle)
            eet_writer = csv.writer(eet_handle)

            pointer_writer.writerow([
                "participant_code", "session_id", "trial_id", "condition_code",
                "run_code", "condition_name", "study", "environment",
                "feedback_timing", "modality", "practice",
                "trial_started_at_epoch", "host_time_epoch", "host_time_iso_utc",
                "trial_elapsed_s", "host_monotonic", "pv_frame_index",
                "pv_timestamp_hl2ss", "eet_timestamp_hl2ss", "pv_eet_delta_ms",
                "eye_calibration_valid", "combined_gaze_valid",
                "projection_valid", "pointer_in_frame", "gaze_pixel_x_raw",
                "gaze_pixel_y_raw", "overlay_pixel_x_drawn",
                "overlay_pixel_y_drawn", "assumed_projection_distance_m",
                "combined_origin_x", "combined_origin_y", "combined_origin_z",
                "combined_direction_x", "combined_direction_y", "combined_direction_z",
            ])
            eet_writer.writerow([
                "participant_code", "session_id", "trial_id", "condition_code",
                "run_code", "condition_name", "study", "environment",
                "feedback_timing", "modality", "practice",
                "trial_started_at_epoch", "host_time_epoch", "host_time_iso_utc",
                "trial_elapsed_s", "host_monotonic", "eet_sample_index",
                "eet_timestamp_hl2ss", "calibration_valid",
                "combined_valid", "combined_origin_x", "combined_origin_y",
                "combined_origin_z", "combined_direction_x", "combined_direction_y",
                "combined_direction_z", "left_valid", "left_origin_x",
                "left_origin_y", "left_origin_z", "left_direction_x",
                "left_direction_y", "left_direction_z", "right_valid",
                "right_origin_x", "right_origin_y", "right_origin_z",
                "right_direction_x", "right_direction_y", "right_direction_z",
            ])

            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            video_writer = cv2.VideoWriter(
                str(video_path),
                fourcc,
                float(self._camera_fps),
                (int(self._width), int(self._height)),
            )
            if not video_writer.isOpened():
                pointer_handle.close()
                eet_handle.close()
                raise RuntimeError(
                    "OpenCV could not open the MP4 video writer for the HoloLens overlay recording."
                )

            self._trial_recording = _HoloLensTrialRecording(
                trial_id=trial.trial_id,
                participant_code=trial.participant_code,
                session_id=trial.session_id,
                condition_code=trial.condition_code,
                run_code=trial.run_code,
                condition_name=trial.condition_name,
                study=trial.condition.study.value,
                environment=trial.condition.environment.value,
                feedback_timing=trial.condition.feedback_timing.value,
                modality=trial.condition.modality.value,
                practice=trial.practice,
                trial_started_at=float(trial.started_at),
                recording_started_at=started,
                recording_dir=recording_dir,
                video_path=video_path,
                pointer_csv_path=pointer_csv_path,
                eet_csv_path=eet_csv_path,
                metadata_path=metadata_path,
                video_writer=video_writer,
                pointer_handle=pointer_handle,
                pointer_writer=pointer_writer,
                eet_handle=eet_handle,
                eet_writer=eet_writer,
                last_flush_monotonic=time.monotonic(),
            )
            self._write_recording_metadata_locked(ended_at=None, reason="recording_started")

        self._log(
            f"HOLOLENS RECORDING STARTED: {trial.trial_id} -> {recording_dir}"
        )
        self._emit_stats(force=True)
        return {
            "video": video_path,
            "pointer_csv": pointer_csv_path,
            "eet_csv": eet_csv_path,
            "metadata": metadata_path,
        }

    def stop_trial_recording(
        self, trial_id: str | None = None, reason: str = "trial_ended"
    ) -> dict[str, Any] | None:
        """Flush and close the active HoloLens trial recording."""
        with self._recording_lock:
            rec = self._trial_recording
            if rec is None:
                return None
            if trial_id is not None and rec.trial_id != trial_id:
                return None

            ended = time.time()
            for handle in (rec.pointer_handle, rec.eet_handle):
                try:
                    handle.flush()
                    handle.close()
                except Exception:
                    logger.exception("Could not close HoloLens recording CSV")
            try:
                rec.video_writer.release()
            except Exception:
                logger.exception("Could not finalize HoloLens MP4 writer")

            self._write_recording_metadata_locked(ended_at=ended, reason=reason)
            summary = {
                "trial_id": rec.trial_id,
                "recording_dir": str(rec.recording_dir),
                "video_path": str(rec.video_path),
                "pointer_csv_path": str(rec.pointer_csv_path),
                "eet_csv_path": str(rec.eet_csv_path),
                "metadata_path": str(rec.metadata_path),
                "video_frame_count": rec.video_frame_count,
                "pointer_row_count": rec.pointer_row_count,
                "eet_row_count": rec.eet_row_count,
                "started_at": rec.recording_started_at,
                "ended_at": ended,
                "reason": reason,
            }
            self._trial_recording = None

        self._log(
            f"HOLOLENS RECORDING STOPPED: {summary['trial_id']} — "
            f"{summary['video_frame_count']} video frames, "
            f"{summary['eet_row_count']} EET samples -> {summary['recording_dir']}"
        )
        self._emit_stats(force=True)
        return summary

    def _record_eet_sample(self, eye: dict[str, Any], *, host_monotonic: float) -> None:
        host_epoch = time.time()
        with self._recording_lock:
            rec = self._trial_recording
            if rec is None:
                return
            combined = self._ray_components(eye, "combined")
            left = self._ray_components(eye, "left")
            right = self._ray_components(eye, "right")
            rec.eet_row_count += 1
            rec.eet_writer.writerow([
                rec.participant_code, rec.session_id, rec.trial_id, rec.condition_code,
                rec.run_code, rec.condition_name, rec.study, rec.environment,
                rec.feedback_timing, rec.modality, int(rec.practice),
                rec.trial_started_at, host_epoch, self._iso_utc(host_epoch),
                max(0.0, host_epoch - rec.trial_started_at), host_monotonic,
                rec.eet_row_count, eye.get("timestamp"), int(bool(eye.get("calibration_valid"))),
                int(bool(eye.get("combined_valid"))), *combined,
                int(bool(eye.get("left_valid"))), *left,
                int(bool(eye.get("right_valid"))), *right,
            ])
            self._flush_recording_if_needed_locked(host_monotonic)

    def _record_pv_frame(
        self, frame: np.ndarray, camera: dict[str, Any], *, host_monotonic: float
    ) -> None:
        host_epoch = time.time()
        # Copy the short history outside the recorder lock so stream ingestion is
        # never blocked by MP4 encoding while holding the live-data lock.
        with self._lock:
            eye_history = list(self._eye_history)
        eye = self._nearest_eye_sample(eye_history, camera.get("timestamp")) or {}
        overlay = self.project_gaze_to_pv(
            eye, camera, distance_m=self._gaze_projection_distance_m
        )

        with self._recording_lock:
            rec = self._trial_recording
            if rec is None:
                return

            drawn_x = drawn_y = None
            raw_pixel = overlay.get("pixel")
            if overlay.get("valid") and overlay.get("in_frame") and raw_pixel:
                raw_x, raw_y = float(raw_pixel[0]), float(raw_pixel[1])
                if rec.smoothed_gaze is None:
                    drawn_x, drawn_y = raw_x, raw_y
                else:
                    alpha = 0.42
                    drawn_x = alpha * raw_x + (1.0 - alpha) * rec.smoothed_gaze[0]
                    drawn_y = alpha * raw_y + (1.0 - alpha) * rec.smoothed_gaze[1]
                rec.smoothed_gaze = (drawn_x, drawn_y)
            else:
                raw_x = raw_y = None
                rec.smoothed_gaze = None

            annotated = self._draw_recording_gaze_overlay(
                frame, drawn_x=drawn_x, drawn_y=drawn_y
            )
            # Guard against an unexpected stream resolution change.
            if annotated.shape[1] != self._width or annotated.shape[0] != self._height:
                annotated = cv2.resize(annotated, (self._width, self._height))
            rec.video_writer.write(annotated)
            rec.video_frame_count += 1
            rec.pointer_row_count += 1

            combined = self._ray_components(eye, "combined")
            rec.pointer_writer.writerow([
                rec.participant_code, rec.session_id, rec.trial_id, rec.condition_code,
                rec.run_code, rec.condition_name, rec.study, rec.environment,
                rec.feedback_timing, rec.modality, int(rec.practice),
                rec.trial_started_at, host_epoch, self._iso_utc(host_epoch),
                max(0.0, host_epoch - rec.trial_started_at), host_monotonic,
                rec.video_frame_count, camera.get("timestamp"), eye.get("timestamp"),
                overlay.get("timestamp_delta_ms"), int(bool(eye.get("calibration_valid"))),
                int(bool(eye.get("combined_valid"))), int(bool(overlay.get("valid"))),
                int(bool(overlay.get("in_frame"))), raw_x, raw_y, drawn_x, drawn_y,
                self._gaze_projection_distance_m, *combined,
            ])
            self._flush_recording_if_needed_locked(host_monotonic)

    @staticmethod
    def _draw_recording_gaze_overlay(
        frame: np.ndarray, *, drawn_x: float | None, drawn_y: float | None
    ) -> np.ndarray:
        annotated = frame.copy()
        if drawn_x is None or drawn_y is None:
            return annotated
        x, y = int(round(drawn_x)), int(round(drawn_y))
        radius = max(10, int(min(annotated.shape[0], annotated.shape[1]) * 0.018))
        # BGR cyan ring + white crosshair, matching the validation window.
        cv2.circle(annotated, (x, y), radius, (255, 255, 0), 4, cv2.LINE_AA)
        cross = max(5, int(radius * 0.55))
        cv2.line(annotated, (x - cross, y), (x + cross, y), (255, 255, 255), 2, cv2.LINE_AA)
        cv2.line(annotated, (x, y - cross), (x, y + cross), (255, 255, 255), 2, cv2.LINE_AA)
        return annotated

    def _flush_recording_if_needed_locked(self, host_monotonic: float) -> None:
        rec = self._trial_recording
        if rec is None or host_monotonic - rec.last_flush_monotonic < 1.0:
            return
        rec.pointer_handle.flush()
        rec.eet_handle.flush()
        rec.last_flush_monotonic = host_monotonic

    def _write_recording_metadata_locked(
        self, *, ended_at: float | None, reason: str
    ) -> None:
        rec = self._trial_recording
        if rec is None:
            return
        payload = {
            "participant_code": rec.participant_code,
            "session_id": rec.session_id,
            "trial_id": rec.trial_id,
            "condition_code": rec.condition_code,
            "run_code": rec.run_code,
            "condition_name": rec.condition_name,
            "study": rec.study,
            "environment": rec.environment,
            "feedback_timing": rec.feedback_timing,
            "modality": rec.modality,
            "practice": rec.practice,
            "trial_started_at_epoch": rec.trial_started_at,
            "recording_started_at_epoch": rec.recording_started_at,
            "recording_ended_at_epoch": ended_at,
            "stop_reason": reason,
            "camera": {
                "width": self._width,
                "height": self._height,
                "configured_fps": self._camera_fps,
                "video_codec": "mp4v",
                "video_frame_count": rec.video_frame_count,
                "overlay": "combined EET gaze direction; cyan circle + white crosshair",
            },
            "eye_tracking": {
                "configured_fps": self._eye_fps,
                "eet_sample_count": rec.eet_row_count,
                "pointer_row_count": rec.pointer_row_count,
                "projection_distance_m": self._gaze_projection_distance_m,
                "projection_note": (
                    "The pointer is a pose-registered gaze-direction projection at a fixed "
                    "distance, not a depth-resolved physical surface intersection."
                ),
            },
            "files": {
                "annotated_video": rec.video_path.name,
                "gaze_pointer_csv": rec.pointer_csv_path.name,
                "raw_eet_csv": rec.eet_csv_path.name,
            },
        }
        rec.metadata_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _update_connected_status(self) -> None:
        with self._lock:
            camera_ready = self._camera_ready
            eye_ready = self._eye_ready
        if camera_ready and eye_ready and not self._stop_event.is_set():
            self._set_status(DeviceStatus.RECEIVING_DATA)
            self._progress(100, "Connected: live PV camera + Extended Eye Tracking data received.")
            self._log("HoloLens connection validated by live camera and eye-gaze packets.")
            self._emit_stats(force=True)

    def _handle_stream_error(self, stream_name: str, exc: Exception) -> None:
        if self._stop_event.is_set():
            return
        message = f"{stream_name} stream error: {exc}"
        logger.exception(message)
        with self._lock:
            self._last_error = message
        if self.status in (DeviceStatus.CONNECTING, DeviceStatus.CONNECTED):
            self._set_status(DeviceStatus.ERROR)
        else:
            self._set_status(DeviceStatus.WARNING)
        self._log(message)
        self._emit_stats(force=True)

    def _fail_connection(self, message: str) -> None:
        logger.error(message)
        with self._lock:
            self._last_error = message
        self._set_status(DeviceStatus.ERROR)
        self._progress(0, message)
        self._log(message)
        self._emit_stats(force=True)

    def _progress(self, percent: int, text: str) -> None:
        self.connection_progress.emit(max(0, min(100, int(percent))), str(text))

    def _log(self, text: str) -> None:
        logger.info(text)
        self.log_message.emit(str(text))

    # ------------------------------------------------------------------
    # Public live data / health facade

    def latest_camera_frame(self):
        with self._lock:
            return self._latest_camera_frame

    def latest_eye_data(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._latest_eye)

    def stats(self) -> dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            camera_age = None if self._last_camera_monotonic is None else max(0.0, now - self._last_camera_monotonic)
            eye_age = None if self._last_eye_monotonic is None else max(0.0, now - self._last_eye_monotonic)
            camera_elapsed = None if self._camera_started_monotonic is None else max(0.001, now - self._camera_started_monotonic)
            eye_elapsed = None if self._eye_started_monotonic is None else max(0.001, now - self._eye_started_monotonic)
            return {
                "host": self._host,
                "client_dir": self._client_dir,
                "camera_resolution": f"{self._width}x{self._height}",
                "camera_fps_configured": self._camera_fps,
                "eye_fps_configured": self._eye_fps,
                "camera_frame_count": self._camera_frame_count,
                "eye_packet_count": self._eye_packet_count,
                "camera_rate_hz": 0.0 if not camera_elapsed else self._camera_frame_count / camera_elapsed,
                "eye_rate_hz": 0.0 if not eye_elapsed else self._eye_packet_count / eye_elapsed,
                "last_camera_age_s": camera_age,
                "last_eye_age_s": eye_age,
                "latest_camera_timestamp": self._latest_camera_timestamp,
                "latest_eye": dict(self._latest_eye),
                "camera_ready": self._camera_ready,
                "eye_ready": self._eye_ready,
                "last_error": self._last_error,
                "trial_recording_active": self._trial_recording is not None,
                "trial_recording_id": (
                    None if self._trial_recording is None else self._trial_recording.trial_id
                ),
            }

    def check_connection(self, max_age_s: float = 2.0) -> tuple[bool, str]:
        stats = self.stats()
        camera_age = stats["last_camera_age_s"]
        eye_age = stats["last_eye_age_s"]
        ok = (
            self.status in (DeviceStatus.RECEIVING_DATA, DeviceStatus.WARNING)
            and stats["camera_ready"]
            and stats["eye_ready"]
            and camera_age is not None
            and eye_age is not None
            and float(camera_age) <= max_age_s
            and float(eye_age) <= max_age_s
        )
        if ok:
            calibration = bool((stats.get("latest_eye") or {}).get("calibration_valid", False))
            calibration_text = "eye calibration valid" if calibration else "eye calibration NOT valid"
            if self.status == DeviceStatus.WARNING:
                self._set_status(DeviceStatus.RECEIVING_DATA)
            return (
                True,
                f"Live HoloLens connection verified: PV frame {float(camera_age):.2f} s ago, "
                f"eye packet {float(eye_age):.2f} s ago; {calibration_text}.",
            )

        if self.status == DeviceStatus.RECEIVING_DATA:
            self._set_status(DeviceStatus.WARNING)
        camera_text = "none" if camera_age is None else f"{float(camera_age):.2f} s ago"
        eye_text = "none" if eye_age is None else f"{float(eye_age):.2f} s ago"
        return (
            False,
            "HoloLens live-data check failed. "
            f"Last PV frame: {camera_text}; last eye packet: {eye_text}. "
            "Keep the hl2ss app open on the headset and reconnect if either stream has stopped.",
        )

    def _emit_stats(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_stats_emit < 0.25:
            return
        self._last_stats_emit = now
        self.stream_stats_changed.emit(self.stats())
