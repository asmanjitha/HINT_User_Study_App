"""Beam webcam eye-tracker and screen-overlay recorder for HINT.

Beam owns the webcam.  This adapter consumes tracking results from the Beam
Eye Tracker SDK and deliberately does not open the camera through OpenCV. For
each Beam activity it separately captures the selected participant display and
writes an MP4 with the latest valid gaze point overlaid.
"""

from __future__ import annotations

import csv
import json
import logging
import math
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

from PySide6.QtCore import Signal

from devices.base_device import BaseDevice
from models.enums import DeviceStatus, DeviceType
from models.trial import Trial

logger = logging.getLogger(__name__)


class BeamConnectionError(RuntimeError):
    """Raised when Beam or its SDK is unavailable."""


@dataclass
class _BeamTrialRecording:
    trial_id: str
    recording_dir: Path
    csv_path: Path
    metadata_path: Path
    csv_file: Any
    writer: csv.DictWriter
    video_path: Path
    video_writer: Any
    video_stop_event: threading.Event
    video_thread: threading.Thread | None
    video_width: int
    video_height: int
    capture_viewport: tuple[int, int, int, int]
    capture_target_source: str
    started_utc_ns: int
    started_monotonic_ns: int
    sample_count: int = 0
    valid_sample_count: int = 0
    video_frame_count: int = 0
    video_dropped_frame_count: int = 0
    video_capture_error: str = ""


class BeamEyeTrackerDevice(BaseDevice):
    """Receive and record screen gaze from Beam Eye Tracker SDK 2.2."""

    stats_changed = Signal(object)
    log_message = Signal(str)

    FRIENDLY_NAME = "HINT Study Console"
    CSV_FIELDS = (
        "console_timestamp_utc_ns",
        "console_monotonic_ns",
        "trial_elapsed_seconds",
        "beam_timestamp_seconds",
        "screen_gaze_x_px",
        "screen_gaze_y_px",
        "unbounded_gaze_x_px",
        "unbounded_gaze_y_px",
        "viewport_gaze_x_normalized",
        "viewport_gaze_y_normalized",
        "gaze_confidence",
        "gaze_confidence_value",
        "viewport_gaze_confidence",
        "viewport_gaze_confidence_value",
        "head_confidence",
        "head_confidence_value",
        "head_track_session_uid",
        "head_x_m",
        "head_y_m",
        "head_z_m",
        "head_yaw_rad_derived",
        "head_pitch_rad_derived",
        "head_roll_rad_derived",
        "head_rotation_r00",
        "head_rotation_r01",
        "head_rotation_r02",
        "head_rotation_r10",
        "head_rotation_r11",
        "head_rotation_r12",
        "head_rotation_r20",
        "head_rotation_r21",
        "head_rotation_r22",
        "valid",
    )

    def __init__(
        self,
        parent=None,
        *,
        sdk_loader: Callable[[], ModuleType] | None = None,
        screen_recording_config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(DeviceType.BEAM, parent)
        self._sdk_loader = sdk_loader or self._load_sdk
        self._sdk: ModuleType | None = None
        self._api: Any = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.RLock()
        self._recording: _BeamTrialRecording | None = None
        self._viewport = (0, 0, 1920, 1080)
        self._manual_viewport = self._viewport
        self._auto_follow_participant_window = True
        self._capture_target_source = "initial_default"
        self._last_capture_target_message = ""
        self._latest: dict[str, Any] = {}
        self._last_sample_monotonic = 0.0
        self._sample_count = 0
        self._last_error = ""
        self._reception_status = "NOT_RECEIVING_TRACKING_DATA"
        self._sdk_version = ""
        screen_config = screen_recording_config or {}
        self._screen_recording_enabled = bool(screen_config.get("enabled", True))
        self._screen_recording_fps = max(
            1.0, min(60.0, float(screen_config.get("fps", 30.0)))
        )
        codec = str(screen_config.get("codec", "mp4v"))
        self._screen_recording_codec = codec if len(codec) == 4 else "mp4v"
        self._gaze_pointer_radius = max(
            4, min(80, int(screen_config.get("gaze_pointer_radius_px", 18)))
        )
        self._gaze_stale_seconds = max(
            0.05, min(2.0, float(screen_config.get("gaze_stale_seconds", 0.25)))
        )
        self._show_video_status_overlay = bool(
            screen_config.get("show_status_overlay", True)
        )
        mode = str(
            screen_config.get("capture_display_mode", "auto_participant_window")
        ).strip().lower()
        self._auto_follow_participant_window = mode not in {
            "manual",
            "manual_display",
            "selected_display",
        }

    @staticmethod
    def _load_sdk() -> ModuleType:
        try:
            import eyeware.beam_eye_tracker as beam_sdk
        except (ImportError, OSError) as exc:
            raise BeamConnectionError(
                "Beam Python SDK is not installed. Run: "
                "pip install beam-eye-tracker==2.2.0"
            ) from exc
        return beam_sdk

    @staticmethod
    def sdk_available() -> bool:
        try:
            import eyeware.beam_eye_tracker  # noqa: F401
            return True
        except (ImportError, OSError):
            return False

    def configure_viewport(
        self,
        x: int,
        y: int,
        width: int,
        height: int,
        *,
        remember_manual: bool = True,
        source: str = "manual_display",
    ) -> None:
        if width < 2 or height < 2:
            raise ValueError("Beam viewport width and height must both be at least 2 pixels")
        viewport = (int(x), int(y), int(width), int(height))
        with self._lock:
            if self._recording is not None:
                raise RuntimeError(
                    "Cannot change the Beam capture display while screen_gaze.mp4 is recording"
                )
            self._viewport = viewport
            if remember_manual:
                self._manual_viewport = viewport
            self._capture_target_source = str(source)
            api, sdk = self._api, self._sdk
        if api is not None and sdk is not None:
            api.update_viewport_geometry(self._make_viewport_geometry(sdk))
            self._log(f"Beam viewport updated to x={x}, y={y}, {width}x{height}.")

    def set_capture_target(
        self,
        x: int,
        y: int,
        width: int,
        height: int,
        *,
        auto_follow_participant_window: bool,
    ) -> None:
        """Set the experimenter's fallback display and automatic-follow mode."""
        self.configure_viewport(
            x,
            y,
            width,
            height,
            remember_manual=True,
            source="automatic_fallback" if auto_follow_participant_window else "manual_display",
        )
        with self._lock:
            self._auto_follow_participant_window = bool(auto_follow_participant_window)
            if auto_follow_participant_window:
                self._last_capture_target_message = (
                    "Automatic participant-window capture is enabled. The selected display "
                    "is only a fallback until an activity window is shown."
                )
            else:
                self._last_capture_target_message = (
                    "Beam screen recording is locked to the experimenter-selected display."
                )
        self._emit_stats()

    @staticmethod
    def _rect_overlap_area(
        a: tuple[int, int, int, int], b: tuple[int, int, int, int]
    ) -> int:
        left = max(a[0], b[0])
        top = max(a[1], b[1])
        right = min(a[0] + a[2], b[0] + b[2])
        bottom = min(a[1] + a[3], b[1] + b[3])
        return max(0, right - left) * max(0, bottom - top)

    @classmethod
    def match_display_to_rect(
        cls,
        rect: tuple[int, int, int, int],
        displays: list[dict[str, int | str]],
    ) -> dict[str, int | str] | None:
        """Return the physical display with the largest overlap with *rect*."""
        if not displays:
            return None
        ranked: list[tuple[int, dict[str, int | str]]] = []
        for display in displays:
            display_rect = (
                int(display["x"]),
                int(display["y"]),
                int(display["width"]),
                int(display["height"]),
            )
            ranked.append((cls._rect_overlap_area(rect, display_rect), display))
        ranked.sort(key=lambda item: item[0], reverse=True)
        if ranked[0][0] > 0:
            return ranked[0][1]

        # Fallback for unusual DPI/coordinate virtualization: choose the display
        # whose center is nearest the requested rectangle's center.
        rect_cx = rect[0] + rect[2] / 2.0
        rect_cy = rect[1] + rect[3] / 2.0
        return min(
            displays,
            key=lambda display: (
                (int(display["x"]) + int(display["width"]) / 2.0 - rect_cx) ** 2
                + (int(display["y"]) + int(display["height"]) / 2.0 - rect_cy) ** 2
            ),
        )

    @staticmethod
    def _windows_monitor_rect_for_window(window_handle: int) -> tuple[int, int, int, int] | None:
        """Resolve the monitor containing a native HWND using Windows APIs."""
        if sys.platform != "win32" or not window_handle:
            return None
        try:
            import ctypes
            from ctypes import wintypes

            class RECT(ctypes.Structure):
                _fields_ = [
                    ("left", wintypes.LONG),
                    ("top", wintypes.LONG),
                    ("right", wintypes.LONG),
                    ("bottom", wintypes.LONG),
                ]

            class MONITORINFO(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wintypes.DWORD),
                    ("rcMonitor", RECT),
                    ("rcWork", RECT),
                    ("dwFlags", wintypes.DWORD),
                ]

            user32 = ctypes.windll.user32
            monitor = user32.MonitorFromWindow(wintypes.HWND(window_handle), 2)
            if not monitor:
                return None
            info = MONITORINFO()
            info.cbSize = ctypes.sizeof(MONITORINFO)
            if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                return None
            rect = info.rcMonitor
            return (
                int(rect.left),
                int(rect.top),
                int(rect.right - rect.left),
                int(rect.bottom - rect.top),
            )
        except Exception as exc:
            logger.warning("Could not resolve participant window monitor: %s", exc)
            return None

    def sync_capture_to_participant_window(self, window_handle: int) -> tuple[bool, str]:
        """Point Beam and screen capture at the monitor containing the user window.

        Automatic mode is the default. Manual mode intentionally leaves the
        experimenter-selected display untouched. If automatic resolution fails,
        the selected display remains the safe fallback instead of blocking a trial.
        """
        with self._lock:
            auto = self._auto_follow_participant_window
            manual_viewport = self._manual_viewport
            recording_active = self._recording is not None
        if recording_active:
            message = "Beam capture target cannot be changed after recording has started."
            return False, message
        if not auto:
            message = (
                "Manual Beam capture target is active; keeping the experimenter-selected "
                f"display at x={manual_viewport[0]}, y={manual_viewport[1]}, "
                f"{manual_viewport[2]}x{manual_viewport[3]}."
            )
            with self._lock:
                self._last_capture_target_message = message
            self._log(message)
            self._emit_stats()
            return True, message

        window_rect = self._windows_monitor_rect_for_window(int(window_handle))
        display = self.match_display_to_rect(window_rect, self.available_displays()) if window_rect else None
        if display is None:
            self.configure_viewport(
                *manual_viewport,
                remember_manual=False,
                source="automatic_fallback",
            )
            message = (
                "Could not automatically resolve the participant-window monitor. "
                "Using the experimenter-selected fallback display."
            )
            with self._lock:
                self._last_capture_target_message = message
            self._log(message)
            self._emit_stats()
            return False, message

        viewport = (
            int(display["x"]),
            int(display["y"]),
            int(display["width"]),
            int(display["height"]),
        )
        self.configure_viewport(
            *viewport,
            remember_manual=False,
            source=f"participant_window_{display.get('name', 'display')}",
        )
        message = (
            f"Automatic Beam capture target: {display.get('name', 'display')} at "
            f"({viewport[0]}, {viewport[1]}) {viewport[2]}x{viewport[3]} physical px."
        )
        with self._lock:
            self._last_capture_target_message = message
        self._log(message)
        self._emit_stats()
        return True, message

    @staticmethod
    def available_displays() -> list[dict[str, int | str]]:
        """Return capture displays in physical pixels using MSS.

        Qt may expose device-independent pixels when Windows display scaling is
        enabled. Beam and screen capture both use physical desktop pixels, so
        MSS is the authoritative geometry source for accurate gaze placement.
        """
        try:
            import mss

            with mss.mss() as capture:
                monitors = list(capture.monitors[1:])
            return [
                {
                    "index": index,
                    "name": f"Display {index}",
                    "x": int(monitor["left"]),
                    "y": int(monitor["top"]),
                    "width": int(monitor["width"]),
                    "height": int(monitor["height"]),
                    "geometry_source": "MSS physical pixels",
                }
                for index, monitor in enumerate(monitors, start=1)
            ]
        except Exception as exc:
            logger.warning("Could not enumerate physical displays with MSS: %s", exc)
            return []

    def _make_viewport_geometry(self, sdk: ModuleType):
        x, y, width, height = self._viewport
        return sdk.ViewportGeometry(
            sdk.Point(x, y),
            sdk.Point(x + width - 1, y + height - 1),
        )

    def connect_device(self) -> None:
        if self.status in (
            DeviceStatus.CONNECTING,
            DeviceStatus.CONNECTED,
            DeviceStatus.RECEIVING_DATA,
        ):
            return
        self._last_error = ""
        self._stop_event.clear()
        self._set_status(DeviceStatus.CONNECTING)
        self._thread = threading.Thread(
            target=self._run,
            name="HINT-Beam-Eye-Tracker",
            daemon=True,
        )
        self._thread.start()

    def disconnect_device(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        try:
            self.stop_trial_recording(reason="beam_disconnected")
        except Exception:
            logger.exception("Could not finalize Beam recording during disconnect")
        with self._lock:
            self._api = None
            self._sdk = None
            self._thread = None
            self._reception_status = "NOT_RECEIVING_TRACKING_DATA"
        self._set_status(DeviceStatus.DISCONNECTED)
        self._emit_stats()

    def _run(self) -> None:
        try:
            sdk = self._sdk_loader()
            api = sdk.API(self.FRIENDLY_NAME, self._make_viewport_geometry(sdk))
            try:
                version = api.get_version()
                self._sdk_version = ".".join(
                    str(getattr(version, name, 0))
                    for name in ("major", "minor", "patch", "build")
                )
            except Exception:
                self._sdk_version = "unknown"
            with self._lock:
                self._sdk = sdk
                self._api = api
            self._log("Beam SDK connected. Requesting tracking output from Beam Eye Tracker.")
            api.attempt_starting_the_beam_eye_tracker()
            self._set_status(DeviceStatus.CONNECTED)

            while not self._stop_event.wait(1.0 / 90.0):
                status = api.get_tracking_data_reception_status()
                status_name = self._enum_name(status)
                with self._lock:
                    self._reception_status = status_name
                if status_name != "RECEIVING_TRACKING_DATA":
                    if self.status == DeviceStatus.RECEIVING_DATA:
                        self._set_status(DeviceStatus.WARNING)
                    self._emit_stats()
                    continue

                tracking_state_set = api.get_latest_tracking_state_set()
                user_state = tracking_state_set.user_state()
                null_timestamp = getattr(sdk, "NULL_DATA_TIMESTAMP", None)
                if callable(null_timestamp):
                    null_timestamp = null_timestamp()
                if null_timestamp is not None and user_state.timestamp_in_seconds == null_timestamp:
                    continue
                sample = self.extract_sample(tracking_state_set)
                now_monotonic = time.monotonic()
                # Polling may return the same Beam frame more than once.
                beam_timestamp = sample.get("beam_timestamp_seconds")
                if beam_timestamp == self._latest.get("beam_timestamp_seconds"):
                    continue
                self._accept_sample(sample, now_monotonic=now_monotonic)
                if self.status != DeviceStatus.RECEIVING_DATA:
                    self._set_status(DeviceStatus.RECEIVING_DATA)
        except Exception as exc:
            if not self._stop_event.is_set():
                self._last_error = str(exc)
                logger.exception("Beam Eye Tracker connection failed")
                self._log(f"Beam connection failed: {exc}")
                self._set_status(DeviceStatus.ERROR)
                self._emit_stats()
        finally:
            with self._lock:
                self._api = None
                self._sdk = None

    @classmethod
    def extract_sample(cls, tracking_state_set: Any) -> dict[str, Any]:
        """Convert one SDK TrackingStateSet into stable primitive fields."""
        user = tracking_state_set.user_state()
        screen = user.unified_screen_gaze
        viewport = user.viewport_gaze
        head = user.head_pose
        bounded = screen.point_of_regard
        unbounded = screen.unbounded_point_of_regard
        normalized = viewport.normalized_point_of_regard
        translation = head.translation_from_hcs_to_wcs
        rotation = head.rotation_from_hcs_to_wcs
        gaze_confidence = cls._enum_name(screen.confidence)
        viewport_confidence = cls._enum_name(viewport.confidence)
        head_confidence = cls._enum_name(head.confidence)
        yaw, pitch, roll = cls._rotation_to_euler(rotation)
        valid = gaze_confidence != "LOST_TRACKING"
        return {
            "beam_timestamp_seconds": cls._number(user.timestamp_in_seconds),
            "screen_gaze_x_px": cls._number(bounded.x),
            "screen_gaze_y_px": cls._number(bounded.y),
            "unbounded_gaze_x_px": cls._number(unbounded.x),
            "unbounded_gaze_y_px": cls._number(unbounded.y),
            "viewport_gaze_x_normalized": cls._number(normalized.x),
            "viewport_gaze_y_normalized": cls._number(normalized.y),
            "gaze_confidence": gaze_confidence,
            "gaze_confidence_value": cls._enum_value(screen.confidence),
            "viewport_gaze_confidence": viewport_confidence,
            "viewport_gaze_confidence_value": cls._enum_value(viewport.confidence),
            "head_confidence": head_confidence,
            "head_confidence_value": cls._enum_value(head.confidence),
            "head_track_session_uid": cls._number(head.track_session_uid),
            "head_x_m": cls._number(translation.x),
            "head_y_m": cls._number(translation.y),
            "head_z_m": cls._number(translation.z),
            "head_yaw_rad_derived": yaw,
            "head_pitch_rad_derived": pitch,
            "head_roll_rad_derived": roll,
            **{
                f"head_rotation_r{row}{col}": cls._number(rotation[row][col])
                for row in range(3)
                for col in range(3)
            },
            "valid": int(valid),
        }

    def _accept_sample(self, sample: dict[str, Any], *, now_monotonic: float | None = None) -> None:
        now_monotonic = time.monotonic() if now_monotonic is None else now_monotonic
        utc_ns = time.time_ns()
        monotonic_ns = time.monotonic_ns()
        with self._lock:
            self._latest = dict(sample)
            self._last_sample_monotonic = now_monotonic
            self._sample_count += 1
            recording = self._recording
            if recording is not None:
                row = {field: "" for field in self.CSV_FIELDS}
                row.update(sample)
                row["console_timestamp_utc_ns"] = utc_ns
                row["console_monotonic_ns"] = monotonic_ns
                row["trial_elapsed_seconds"] = (
                    monotonic_ns - recording.started_monotonic_ns
                ) / 1_000_000_000.0
                recording.writer.writerow(row)
                recording.sample_count += 1
                recording.valid_sample_count += int(bool(sample.get("valid")))
                if recording.sample_count % 60 == 0:
                    recording.csv_file.flush()
        if self._sample_count % 6 == 0:
            self._emit_stats()

    def is_stream_healthy(self, max_age_s: float = 1.0) -> bool:
        with self._lock:
            return (
                self._reception_status == "RECEIVING_TRACKING_DATA"
                and self._last_sample_monotonic > 0
                and time.monotonic() - self._last_sample_monotonic <= max_age_s
            )

    def check_connection(self) -> tuple[bool, str]:
        stats = self.stats()
        if not self.sdk_available() and self._sdk is None:
            return False, "Beam SDK is not installed. Run pip install beam-eye-tracker==2.2.0."
        if not self.is_stream_healthy():
            return False, (
                "Beam is not delivering fresh tracking data. Open Beam Eye Tracker, "
                "select and calibrate the webcam, then reconnect or wait for tracking."
            )
        screen_ok, screen_message = self.check_screen_recording()
        if not screen_ok:
            return False, screen_message
        confidence = stats.get("gaze_confidence", "unknown")
        target_mode = stats.get("capture_target_mode", "unknown")
        return True, (
            f"Beam tracking is live (gaze confidence: {confidence}); screen recording "
            f"target mode is {target_mode}. screen_gaze.mp4 capture is ready."
        )

    def check_screen_recording(self) -> tuple[bool, str]:
        if not self._screen_recording_enabled:
            return True, "Beam screen recording is disabled in study.yaml."
        try:
            import cv2  # noqa: F401
            import mss  # noqa: F401
            import numpy  # noqa: F401
        except (ImportError, OSError) as exc:
            return False, (
                "Beam gaze is live, but screen MP4 dependencies are unavailable: "
                f"{exc}. Run pip install -r requirements.txt."
            )
        if not self.available_displays():
            return False, (
                "Beam gaze is live, but no physical display is available to MSS for "
                "screen_gaze.mp4 capture."
            )
        return True, "Beam screen MP4 capture is ready."

    def latest_gaze(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._latest)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            age = (
                time.monotonic() - self._last_sample_monotonic
                if self._last_sample_monotonic
                else None
            )
            return {
                "status": self.status.value,
                "reception_status": self._reception_status,
                "sdk_version": self._sdk_version,
                "viewport": self._viewport,
                "manual_fallback_viewport": self._manual_viewport,
                "capture_target_mode": (
                    "automatic_participant_window"
                    if self._auto_follow_participant_window
                    else "manual_display"
                ),
                "capture_target_source": self._capture_target_source,
                "capture_target_message": self._last_capture_target_message,
                "sample_count": self._sample_count,
                "last_sample_age_seconds": age,
                "last_error": self._last_error,
                "recording_trial_id": self._recording.trial_id if self._recording else None,
                "screen_video_enabled": self._screen_recording_enabled,
                "screen_video_path": (
                    str(self._recording.video_path) if self._recording else None
                ),
                "screen_video_frame_count": (
                    self._recording.video_frame_count if self._recording else 0
                ),
                "screen_video_capture_error": (
                    self._recording.video_capture_error if self._recording else ""
                ),
                **self._latest,
            }

    def _open_screen_video_writer(self, video_path: Path, width: int, height: int):
        try:
            import cv2
        except (ImportError, OSError) as exc:
            raise BeamConnectionError(
                "OpenCV is required for Beam screen MP4 recording. "
                "Run pip install -r requirements.txt."
            ) from exc
        fourcc = cv2.VideoWriter_fourcc(*self._screen_recording_codec)
        writer = cv2.VideoWriter(
            str(video_path),
            fourcc,
            self._screen_recording_fps,
            (width, height),
        )
        if not writer.isOpened():
            writer.release()
            raise BeamConnectionError(
                f"Could not open Beam screen video {video_path} with codec "
                f"{self._screen_recording_codec}."
            )
        return writer

    def _screen_recording_loop(self, recording: _BeamTrialRecording) -> None:
        """Capture the configured display and write gaze-overlay frames."""
        try:
            import cv2
            import mss
            import numpy as np

            viewport_x, viewport_y, _, _ = recording.capture_viewport
            monitor = {
                "left": viewport_x,
                "top": viewport_y,
                "width": recording.video_width,
                "height": recording.video_height,
            }
            frame_interval = 1.0 / self._screen_recording_fps
            next_frame_at = time.monotonic()
            with mss.mss() as capture:
                while not recording.video_stop_event.is_set():
                    now = time.monotonic()
                    wait_seconds = next_frame_at - now
                    if wait_seconds > 0:
                        if recording.video_stop_event.wait(wait_seconds):
                            break
                        now = time.monotonic()
                    elif -wait_seconds >= frame_interval:
                        missed = int((-wait_seconds) // frame_interval)
                        recording.video_dropped_frame_count += missed
                        next_frame_at = now

                    screen_bgra = np.asarray(capture.grab(monitor))
                    frame = np.ascontiguousarray(screen_bgra[:, :, :3])
                    with self._lock:
                        sample = dict(self._latest)
                        sample_age = (
                            now - self._last_sample_monotonic
                            if self._last_sample_monotonic
                            else float("inf")
                        )
                    elapsed = max(
                        0.0,
                        (time.monotonic_ns() - recording.started_monotonic_ns)
                        / 1_000_000_000.0,
                    )
                    self.render_gaze_overlay(
                        frame,
                        sample,
                        viewport=(
                            viewport_x,
                            viewport_y,
                            recording.video_width,
                            recording.video_height,
                        ),
                        sample_age_seconds=sample_age,
                        stale_after_seconds=self._gaze_stale_seconds,
                        radius_px=self._gaze_pointer_radius,
                        show_status=self._show_video_status_overlay,
                        elapsed_seconds=elapsed,
                        cv2_module=cv2,
                    )
                    recording.video_writer.write(frame)
                    recording.video_frame_count += 1
                    if recording.video_frame_count % 30 == 0:
                        self._emit_stats()
                    next_frame_at += frame_interval
        except Exception as exc:
            recording.video_capture_error = str(exc)
            logger.exception("Beam screen recording failed for %s", recording.trial_id)
            self._log(f"Beam screen MP4 recording failed: {exc}")
            self._emit_stats()

    @staticmethod
    def render_gaze_overlay(
        frame: Any,
        sample: dict[str, Any],
        *,
        viewport: tuple[int, int, int, int],
        sample_age_seconds: float,
        stale_after_seconds: float = 0.25,
        radius_px: int = 18,
        show_status: bool = True,
        elapsed_seconds: float = 0.0,
        cv2_module: Any = None,
    ) -> bool:
        """Draw a confidence-colored gaze pointer and return whether it was drawn."""
        if cv2_module is None:
            import cv2 as cv2_module

        confidence = str(
            sample.get("viewport_gaze_confidence")
            or sample.get("gaze_confidence")
            or "LOST_TRACKING"
        ).upper()
        fresh = sample_age_seconds <= stale_after_seconds
        valid = bool(sample.get("valid")) and confidence != "LOST_TRACKING" and fresh
        pointer_drawn = False
        point: tuple[int, int] | None = None
        frame_height, frame_width = frame.shape[:2]

        if valid:
            try:
                norm_x = float(sample["viewport_gaze_x_normalized"])
                norm_y = float(sample["viewport_gaze_y_normalized"])
                if 0.0 <= norm_x <= 1.0 and 0.0 <= norm_y <= 1.0:
                    point = (
                        int(round(norm_x * (frame_width - 1))),
                        int(round(norm_y * (frame_height - 1))),
                    )
            except (KeyError, TypeError, ValueError):
                try:
                    viewport_x, viewport_y, _, _ = viewport
                    point = (
                        int(round(float(sample["screen_gaze_x_px"]))) - viewport_x,
                        int(round(float(sample["screen_gaze_y_px"]))) - viewport_y,
                    )
                    if not (0 <= point[0] < frame_width and 0 <= point[1] < frame_height):
                        point = None
                except (KeyError, TypeError, ValueError):
                    point = None

        if point is not None:
            color = {
                "HIGH": (40, 220, 40),
                "MEDIUM": (0, 215, 255),
                "LOW": (0, 100, 255),
            }.get(confidence, (0, 100, 255))
            radius = max(4, int(radius_px))
            cv2_module.circle(frame, point, radius + 3, (0, 0, 0), 3)
            cv2_module.circle(frame, point, radius, color, 3)
            cv2_module.circle(frame, point, 4, color, -1)
            cv2_module.line(
                frame, (point[0] - radius - 5, point[1]),
                (point[0] + radius + 5, point[1]), color, 2
            )
            cv2_module.line(
                frame, (point[0], point[1] - radius - 5),
                (point[0], point[1] + radius + 5), color, 2
            )
            pointer_drawn = True

        if show_status:
            gaze_status = confidence if pointer_drawn else ("STALE" if not fresh else "LOST")
            label = f"HINT Beam | {elapsed_seconds:0.2f}s | Gaze {gaze_status}"
            (text_width, text_height), baseline = cv2_module.getTextSize(
                label, cv2_module.FONT_HERSHEY_SIMPLEX, 0.55, 1
            )
            cv2_module.rectangle(
                frame, (8, 8), (18 + text_width, 20 + text_height + baseline),
                (0, 0, 0), -1
            )
            cv2_module.putText(
                frame, label, (13, 15 + text_height),
                cv2_module.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1,
                cv2_module.LINE_AA
            )
        return pointer_drawn

    def start_trial_recording(self, trial: Trial) -> dict[str, Any]:
        if trial.trial_path is None:
            raise ValueError("Trial has no storage directory")
        if trial.started_at is None:
            raise ValueError("Trial must be started before Beam recording begins")
        if not self.is_stream_healthy():
            raise BeamConnectionError("Beam is not receiving fresh tracking data")

        with self._lock:
            if self._recording is not None:
                if self._recording.trial_id == trial.trial_id:
                    return {
                        "gaze_csv": self._recording.csv_path,
                        "screen_video": self._recording.video_path,
                        "screen_video_error": self._recording.video_capture_error,
                        "metadata": self._recording.metadata_path,
                    }
                raise RuntimeError(f"Beam is already recording {self._recording.trial_id}")

            recording_dir = trial.trial_path / "sensors" / "beam"
            recording_dir.mkdir(parents=True, exist_ok=True)
            csv_path = recording_dir / "gaze.csv"
            video_path = recording_dir / "screen_gaze.mp4"
            metadata_path = recording_dir / "recording_metadata.json"
            csv_file = csv_path.open("w", newline="", encoding="utf-8", buffering=1)
            writer = csv.DictWriter(csv_file, fieldnames=self.CSV_FIELDS)
            writer.writeheader()
            started_utc_ns = time.time_ns()
            started_monotonic_ns = time.monotonic_ns()
            capture_viewport = tuple(self._viewport)
            _, _, viewport_width, viewport_height = capture_viewport
            # Common MP4 encoders require even frame dimensions.
            video_width = viewport_width - (viewport_width % 2)
            video_height = viewport_height - (viewport_height % 2)
            video_error = ""
            try:
                video_writer = (
                    self._open_screen_video_writer(video_path, video_width, video_height)
                    if self._screen_recording_enabled
                    else None
                )
            except Exception as exc:
                # Preserve raw gaze collection even if this PC cannot initialize
                # its requested MP4 codec. The error is prominent in metadata,
                # device diagnostics, and the master event log.
                video_writer = None
                video_error = str(exc)
                logger.exception("Could not initialize Beam screen MP4")
            recording = _BeamTrialRecording(
                trial_id=trial.trial_id,
                recording_dir=recording_dir,
                csv_path=csv_path,
                metadata_path=metadata_path,
                csv_file=csv_file,
                writer=writer,
                video_path=video_path,
                video_writer=video_writer,
                video_stop_event=threading.Event(),
                video_thread=None,
                video_width=video_width,
                video_height=video_height,
                capture_viewport=capture_viewport,
                capture_target_source=self._capture_target_source,
                started_utc_ns=started_utc_ns,
                started_monotonic_ns=started_monotonic_ns,
                video_capture_error=video_error,
            )
            self._recording = recording
            self._write_metadata(
                recording,
                trial=trial,
                stopped_utc_ns=None,
                reason=None,
            )
            if self._screen_recording_enabled and video_writer is not None:
                recording.video_thread = threading.Thread(
                    target=self._screen_recording_loop,
                    args=(recording,),
                    name=f"HINT-Beam-Screen-{trial.trial_id}",
                    daemon=True,
                )
                recording.video_thread.start()
        self._log(
            f"Beam trial recording started: gaze={csv_path}; "
            f"screen_video={video_path if video_writer is not None else 'unavailable'}"
        )
        if video_error:
            self._log(f"Beam gaze CSV is active, but screen MP4 is unavailable: {video_error}")
        self._emit_stats()
        return {
            "gaze_csv": csv_path,
            "screen_video": video_path,
            "screen_video_error": video_error,
            "metadata": metadata_path,
            "capture_viewport": capture_viewport,
            "capture_target_source": recording.capture_target_source,
        }

    def stop_trial_recording(
        self, trial_id: str | None = None, reason: str = "trial_ended"
    ) -> dict[str, Any] | None:
        with self._lock:
            recording = self._recording
            if recording is None:
                return None
            if trial_id is not None and trial_id != recording.trial_id:
                return None
            self._recording = None
            recording.video_stop_event.set()

        video_thread = recording.video_thread
        if (
            video_thread is not None
            and video_thread.is_alive()
            and video_thread is not threading.current_thread()
        ):
            video_thread.join(timeout=5.0)
            if video_thread.is_alive():
                recording.video_capture_error = (
                    recording.video_capture_error
                    or "Screen recording thread did not stop within 5 seconds"
                )
        if recording.video_writer is not None:
            try:
                recording.video_writer.release()
            except Exception as exc:
                recording.video_capture_error = (
                    recording.video_capture_error or f"Could not finalize MP4: {exc}"
                )
                logger.exception("Could not release Beam screen video writer")
        try:
            recording.csv_file.flush()
        finally:
            recording.csv_file.close()
        summary = {
            "trial_id": recording.trial_id,
            "recording_dir": recording.recording_dir,
            "path": recording.csv_path,
            "screen_video_path": recording.video_path,
            "sample_count": recording.sample_count,
            "valid_sample_count": recording.valid_sample_count,
            "video_frame_count": recording.video_frame_count,
            "video_dropped_frame_count": recording.video_dropped_frame_count,
            "video_capture_error": recording.video_capture_error,
            "reason": reason,
        }
        self._write_metadata(
            recording,
            trial=None,
            stopped_utc_ns=time.time_ns(),
            reason=reason,
        )
        self._log(
            f"Beam recording stopped: {recording.sample_count} samples "
            f"({recording.valid_sample_count} valid), "
            f"{recording.video_frame_count} screen-video frames."
        )
        self._emit_stats()
        return summary

    def _write_metadata(
        self,
        recording: _BeamTrialRecording,
        *,
        trial: Trial | None,
        stopped_utc_ns: int | None,
        reason: str | None,
    ) -> None:
        existing: dict[str, Any] = {}
        if recording.metadata_path.exists():
            try:
                existing = json.loads(recording.metadata_path.read_text(encoding="utf-8"))
            except Exception:
                existing = {}
        payload = {
            **existing,
            "sensor": "Beam Eye Tracker",
            "sdk_target_version": "2.2.0",
            "sdk_runtime_version": self._sdk_version,
            "friendly_name": self.FRIENDLY_NAME,
            "trial_id": recording.trial_id,
            "started_utc_ns": recording.started_utc_ns,
            "started_at_utc": datetime.fromtimestamp(
                recording.started_utc_ns / 1_000_000_000, tz=timezone.utc
            ).isoformat(),
            "stopped_utc_ns": stopped_utc_ns,
            "stop_reason": reason,
            "sample_count": recording.sample_count,
            "valid_sample_count": recording.valid_sample_count,
            "viewport_x_y_width_height": list(recording.capture_viewport),
            "capture_target_mode": (
                "automatic_participant_window"
                if self._auto_follow_participant_window
                else "manual_display"
            ),
            "capture_target_source": recording.capture_target_source,
            "manual_fallback_viewport_x_y_width_height": list(self._manual_viewport),
            "capture_target_message": self._last_capture_target_message,
            "screen_video_recorded": self._screen_recording_enabled,
            "screen_video_path": str(recording.video_path),
            "screen_video_codec": self._screen_recording_codec,
            "screen_video_target_fps": self._screen_recording_fps,
            "screen_video_width": recording.video_width,
            "screen_video_height": recording.video_height,
            "screen_video_frame_count": recording.video_frame_count,
            "screen_video_dropped_frame_count": recording.video_dropped_frame_count,
            "screen_video_capture_error": recording.video_capture_error,
            "gaze_pointer_radius_px": self._gaze_pointer_radius,
            "gaze_pointer_stale_after_seconds": self._gaze_stale_seconds,
            "gaze_pointer_confidence_colors_bgr": {
                "HIGH": [40, 220, 40],
                "MEDIUM": [0, 215, 255],
                "LOW": [0, 100, 255],
                "LOST_TRACKING": "pointer hidden",
            },
            "coordinates": {
                "screen": "Windows unified desktop pixels",
                "viewport": "configured participant-screen viewport normalized to [0,1]",
                "head_translation": "meters",
                "head_rotation": "raw Beam 3x3 HCS-to-WCS matrix; Euler fields are derived ZYX radians",
            },
            "webcam_video_recorded": False,
        }
        if trial is not None:
            payload["trial"] = trial.to_metadata_dict()
        recording.metadata_path.write_text(
            json.dumps(payload, indent=2, default=str), encoding="utf-8"
        )

    def _emit_stats(self) -> None:
        self.stats_changed.emit(self.stats())

    def _log(self, message: str) -> None:
        logger.info("Beam: %s", message)
        self.log_message.emit(message)

    @staticmethod
    def _enum_name(value: Any) -> str:
        return str(getattr(value, "name", value)).split(".")[-1]

    @staticmethod
    def _enum_value(value: Any) -> Any:
        return getattr(value, "value", value)

    @staticmethod
    def _number(value: Any) -> Any:
        raw = getattr(value, "value", value)
        try:
            return float(raw)
        except (TypeError, ValueError):
            return raw

    @staticmethod
    def _rotation_to_euler(rotation: Any) -> tuple[float, float, float]:
        """Return conventional ZYX yaw, pitch, roll derived from a 3x3 matrix."""
        r00, r10, r20 = (float(rotation[i][0]) for i in range(3))
        r21, r22 = float(rotation[2][1]), float(rotation[2][2])
        r11, r12 = float(rotation[1][1]), float(rotation[1][2])
        pitch = math.asin(max(-1.0, min(1.0, -r20)))
        if abs(math.cos(pitch)) > 1e-6:
            roll = math.atan2(r21, r22)
            yaw = math.atan2(r10, r00)
        else:
            roll = math.atan2(-r12, r11)
            yaw = 0.0
        return yaw, pitch, roll
