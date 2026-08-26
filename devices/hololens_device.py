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

import importlib
import logging
import socket
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

import numpy as np

from PySide6.QtCore import Signal

from devices.base_device import BaseDevice
from models.enums import DeviceStatus, DeviceType

logger = logging.getLogger(__name__)


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
