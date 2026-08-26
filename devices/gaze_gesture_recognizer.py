"""HoloLens 2 eye-gaze gesture recognizer for HINT feedback commands.

The recognizer consumes the live HL2SS Extended Eye Tracking samples already
owned by :class:`devices.hololens_device.HoloLensDevice`.  It intentionally
does not open another HoloLens stream.

Gestures implemented for the study protocol:

* requested feedback: windowed gaze-likelihood -> UP/DOWN/LEFT/RIGHT;
* anytime pause: two short blinks -> pause;
* state-selection delimiter: close both eyes for about one second;
* state selection: blink N times, then keep eyes open briefly -> box N;
* corrective direction: windowed gaze-likelihood classification.

HoloLens 2 EET does not expose a reliable eyelid-openness channel through the
current HL2SS path, so blink/eye-close detection uses short intervals where
all gaze rays are invalid.  Raw EET data remains recorded separately, allowing
these command events to be audited after a session.
"""

from __future__ import annotations

import math
import time
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal


GAZE_CONTEXT_IDLE = "idle"
GAZE_CONTEXT_DIRECTION = "direction"
GAZE_CONTEXT_DOUBLE_BLINK = "double_blink"
GAZE_CONTEXT_LONG_CLOSE = "long_close"
GAZE_CONTEXT_BLINK_COUNT = "blink_count"


class EyeGazeGestureRecognizer(QObject):
    """Convert live EET samples into the small gesture vocabulary used by HINT."""

    gesture_observed = Signal(object)
    command_recognized = Signal(object)
    direction_debug = Signal(object)
    recognition_error = Signal(str)

    def __init__(self, hololens_device, config: dict | None = None, parent=None) -> None:
        super().__init__(parent)
        self._device = hololens_device
        cfg = dict(config or {})

        self.poll_interval_ms = int(cfg.get("poll_interval_ms", 20))
        self.min_blink_seconds = float(cfg.get("min_blink_seconds", 0.06))
        self.max_blink_seconds = float(cfg.get("max_blink_seconds", 0.45))
        self.long_close_seconds = float(cfg.get("long_close_seconds", 0.85))
        self.double_blink_window_seconds = float(
            cfg.get("double_blink_window_seconds", 1.10)
        )
        self.blink_count_finish_gap_seconds = float(
            cfg.get("blink_count_finish_gap_seconds", 1.15)
        )
        self.direction_threshold_deg = float(cfg.get("direction_threshold_deg", 12.0))
        self.direction_neutral_deg = float(cfg.get("direction_neutral_deg", 6.0))
        self.direction_dominance_margin_deg = float(
            cfg.get("direction_dominance_margin_deg", 2.0)
        )
        self.direction_window_seconds = float(
            cfg.get("direction_window_seconds", 0.70)
        )
        self.direction_min_valid_samples = max(1, int(
            cfg.get("direction_min_valid_samples", 5)
        ))
        self.direction_probability_threshold = float(
            cfg.get("direction_probability_threshold", 0.70)
        )
        self.direction_probability_margin = float(
            cfg.get("direction_probability_margin", 0.20)
        )
        self.center_adaptation_alpha = float(
            cfg.get("center_adaptation_alpha", 0.05)
        )
        self.direction_debug_enabled = bool(
            cfg.get("direction_debug_enabled", True)
        )
        self.direction_debug_stale_seconds = max(0.05, float(
            cfg.get("direction_debug_stale_seconds", 0.25)
        ))

        self._context = GAZE_CONTEXT_IDLE
        self._last_sample_timestamp: Any = None
        self._closed = False
        self._closed_started: float | None = None
        self._blink_times: list[float] = []
        self._blink_count = 0
        self._last_blink_time: float | None = None
        self._direction_center_horizontal: float | None = None
        self._direction_center_vertical: float | None = None
        self._direction_samples: list[dict[str, Any]] = []
        self._direction_last_status_emit: float | None = None
        self._direction_latched = False
        self._last_fresh_sample_monotonic: float | None = None
        self._direction_last_stale_debug_emit: float | None = None

        self._timer = QTimer(self)
        self._timer.setInterval(max(5, self.poll_interval_ms))
        self._timer.timeout.connect(self._poll)

    @property
    def context(self) -> str:
        return self._context

    def set_context(self, context: str) -> None:
        context = str(context or GAZE_CONTEXT_IDLE)
        if context == self._context:
            # Re-entering a context is deliberately a reset.  This is useful after
            # an invalid blink count or a rejected command.
            self._reset_context_state()
        else:
            self._context = context
            self._reset_context_state()

        if self._context == GAZE_CONTEXT_IDLE:
            self._timer.stop()
        else:
            # QTimer.start() is idempotent for an already-running timer.
            self._timer.start()

    def stop(self) -> None:
        self._context = GAZE_CONTEXT_IDLE
        self._timer.stop()
        self._reset_context_state()

    def _reset_context_state(self) -> None:
        self._last_sample_timestamp = None
        self._closed = False
        self._closed_started = None
        self._blink_times = []
        self._blink_count = 0
        self._last_blink_time = None
        self._direction_center_horizontal = None
        self._direction_center_vertical = None
        self._direction_samples = []
        self._direction_last_status_emit = None
        self._direction_latched = False
        self._last_fresh_sample_monotonic = None
        self._direction_last_stale_debug_emit = None

    def _poll(self) -> None:
        now = time.monotonic()
        try:
            eye = self._device.latest_eye_data()
        except Exception as exc:  # pragma: no cover - hardware failure path
            self.recognition_error.emit(f"Eye-gaze input error: {exc}")
            return

        if eye:
            self.process_sample(eye, now=now)
        elif self._context == GAZE_CONTEXT_DIRECTION:
            self._emit_stale_direction_debug(now=now, reason="no_eye_data")
        self.tick(now=now)

    def tick(self, *, now: float | None = None) -> None:
        """Advance time-based recognition (mainly blink-count finalization)."""
        now = time.monotonic() if now is None else float(now)
        if (
            self._context == GAZE_CONTEXT_BLINK_COUNT
            and self._blink_count > 0
            and self._last_blink_time is not None
            and not self._closed
            and now - self._last_blink_time >= self.blink_count_finish_gap_seconds
        ):
            count = self._blink_count
            self._blink_count = 0
            self._last_blink_time = None
            payload = {
                "context": self._context,
                "gesture": "blink_count",
                "count": count,
                "command": str(count),
            }
            self.gesture_observed.emit(dict(payload))
            self.command_recognized.emit(payload)

    def process_sample(self, eye: dict[str, Any], *, now: float | None = None) -> None:
        """Process one fresh EET sample. Public to keep hardware-free tests simple."""
        if self._context == GAZE_CONTEXT_IDLE:
            return

        now = time.monotonic() if now is None else float(now)
        timestamp = eye.get("timestamp")
        if timestamp is not None and timestamp == self._last_sample_timestamp:
            if self._context == GAZE_CONTEXT_DIRECTION:
                self._emit_stale_direction_debug(now=now, reason="duplicate_timestamp")
            return
        if timestamp is not None:
            self._last_sample_timestamp = timestamp
        self._last_fresh_sample_monotonic = now

        # A sample without valid calibration is not interpreted as a deliberate
        # eye closure. Otherwise a headset tracking/calibration failure could be
        # turned into participant commands.
        if not bool(eye.get("calibration_valid", False)):
            self._closed = False
            self._closed_started = None
            if self._context == GAZE_CONTEXT_DIRECTION:
                self._emit_direction_debug({
                    "status": "invalid",
                    "reason": "calibration_invalid",
                    "timestamp": timestamp,
                    "calibration_valid": False,
                    "combined_valid": bool(eye.get("combined_valid", False)),
                    "left_valid": bool(eye.get("left_valid", False)),
                    "right_valid": bool(eye.get("right_valid", False)),
                })
            return

        closed = self._eyes_closed(eye)

        # Direction recognition treats missing/invalid gaze rays as missing data,
        # not as a blink.  Otherwise an intermittent EET stream could discard the
        # first valid sample after every dropout.  Blink semantics are only needed
        # in the explicit blink/eye-close contexts below.
        if self._context == GAZE_CONTEXT_DIRECTION:
            self._closed = False
            self._closed_started = None
            if closed:
                self._emit_direction_debug({
                    "status": "invalid",
                    "reason": "no_valid_gaze_rays",
                    "timestamp": timestamp,
                    "calibration_valid": True,
                    "combined_valid": bool(eye.get("combined_valid", False)),
                    "left_valid": bool(eye.get("left_valid", False)),
                    "right_valid": bool(eye.get("right_valid", False)),
                })
                return
            self._handle_direction(eye, now=now)
            return

        if closed:
            if not self._closed:
                self._closed = True
                self._closed_started = now
            return

        if self._closed:
            started = self._closed_started
            self._closed = False
            self._closed_started = None
            if started is not None:
                duration = max(0.0, now - started)
                self._handle_eye_reopen(duration=duration, now=now)
            # Do not also interpret the first post-blink sample as a directional
            # look. Requiring a subsequent fresh sample makes gestures less noisy.
            return


    def _emit_direction_debug(self, payload: dict[str, Any]) -> None:
        """Emit detailed troubleshooting data for direction recognition."""
        if not self.direction_debug_enabled or self._context != GAZE_CONTEXT_DIRECTION:
            return
        data = {"context": self._context, "debug": "direction_sample"}
        data.update(payload)
        self.direction_debug.emit(data)

    def _emit_stale_direction_debug(self, *, now: float, reason: str) -> None:
        if not self.direction_debug_enabled or self._context != GAZE_CONTEXT_DIRECTION:
            return
        if self._last_fresh_sample_monotonic is None:
            age = None
        else:
            age = max(0.0, now - self._last_fresh_sample_monotonic)
            if age < self.direction_debug_stale_seconds:
                return
        if (
            self._direction_last_stale_debug_emit is not None
            and now - self._direction_last_stale_debug_emit < self.direction_debug_stale_seconds
        ):
            return
        self._direction_last_stale_debug_emit = now
        self._emit_direction_debug({
            "status": "stale",
            "reason": reason,
            "sample_age_seconds": age,
            "last_timestamp": self._last_sample_timestamp,
        })

    @staticmethod
    def _eyes_closed(eye: dict[str, Any]) -> bool:
        return not (
            bool(eye.get("combined_valid", False))
            or bool(eye.get("left_valid", False))
            or bool(eye.get("right_valid", False))
        )

    def _handle_eye_reopen(self, *, duration: float, now: float) -> None:
        if self._context == GAZE_CONTEXT_LONG_CLOSE:
            if duration >= self.long_close_seconds:
                payload = {
                    "context": self._context,
                    "gesture": "long_close",
                    "duration_seconds": duration,
                    "command": "begin_blink_count",
                }
                self.gesture_observed.emit(dict(payload))
                self.command_recognized.emit(payload)
            return

        if not (self.min_blink_seconds <= duration <= self.max_blink_seconds):
            return

        if self._context == GAZE_CONTEXT_DOUBLE_BLINK:
            self._blink_times = [
                t for t in self._blink_times
                if now - t <= self.double_blink_window_seconds
            ]
            self._blink_times.append(now)
            count = len(self._blink_times)
            self.gesture_observed.emit(
                {
                    "context": self._context,
                    "gesture": "blink",
                    "count": count,
                    "duration_seconds": duration,
                }
            )
            if count >= 2:
                self._blink_times = []
                self.command_recognized.emit(
                    {
                        "context": self._context,
                        "gesture": "double_blink",
                        "count": 2,
                        "command": "pause",
                    }
                )
            return

        if self._context == GAZE_CONTEXT_BLINK_COUNT:
            self._blink_count += 1
            self._last_blink_time = now
            self.gesture_observed.emit(
                {
                    "context": self._context,
                    "gesture": "blink",
                    "count": self._blink_count,
                    "duration_seconds": duration,
                }
            )

    def _handle_direction(self, eye: dict[str, Any], *, now: float) -> None:
        """Classify direction from a recent window of valid gaze samples.

        Missing/invalid combined-gaze samples are deliberately ignored instead of
        resetting recognition.  This makes the command robust to intermittent EET
        delivery: confidence is computed only from valid samples that arrived in
        the configured wall-clock window.
        """
        if not bool(eye.get("combined_valid", False)):
            self._emit_direction_debug({
                "status": "invalid",
                "reason": "combined_gaze_invalid",
                "timestamp": eye.get("timestamp"),
                "calibration_valid": bool(eye.get("calibration_valid", False)),
                "combined_valid": False,
                "left_valid": bool(eye.get("left_valid", False)),
                "right_valid": bool(eye.get("right_valid", False)),
            })
            return
        direction = (eye.get("combined") or {}).get("direction")
        if direction is None:
            self._emit_direction_debug({
                "status": "invalid",
                "reason": "combined_direction_missing",
                "timestamp": eye.get("timestamp"),
                "combined_valid": True,
            })
            return
        try:
            raw_x, raw_y, raw_z = (
                float(direction[0]), float(direction[1]), float(direction[2])
            )
            # Extended Eye Tracking gaze rays are in the eye tracker's own
            # coordinate system.  Do NOT interpret raw tracker X/Y directly as
            # screen horizontal/vertical.  Transform the ray into the PV camera
            # frame first; the real headset showed exactly the failure mode this
            # prevents (RIGHT appearing on the old vertical axis and DOWN on the
            # old horizontal axis).
            camera_direction = None
            transform = getattr(self._device, "latest_eye_direction_in_pv_camera", None)
            if callable(transform):
                camera_direction = transform(eye)
                if camera_direction is None:
                    self._emit_direction_debug({
                        "status": "invalid",
                        "reason": "pv_camera_transform_unavailable",
                        "timestamp": eye.get("timestamp"),
                        "raw_x": raw_x, "raw_y": raw_y, "raw_z": raw_z,
                        "coordinate_frame": "tracker_raw",
                    })
                    return

            if camera_direction is not None:
                x, y, z = (float(camera_direction[0]), float(camera_direction[1]), float(camera_direction[2]))
                coordinate_frame = "pv_camera"
            else:
                # Hardware-free/legacy fallback. Production HoloLensDevice has
                # the transform above; tests and old mock devices may not.
                x, y, z = raw_x, raw_y, raw_z
                norm = math.sqrt(x * x + y * y + z * z)
                if not math.isfinite(norm) or norm < 1e-9:
                    self._emit_direction_debug({
                        "status": "invalid",
                        "reason": "invalid_direction_norm",
                        "timestamp": eye.get("timestamp"),
                        "raw_x": raw_x, "raw_y": raw_y, "raw_z": raw_z,
                    })
                    return
                x, y, z = x / norm, y / norm, z / norm
                # Legacy mocks used a conventional +Y-up vector. Convert it to
                # the PV camera convention (+Y-down) used by the classifier.
                y = -y
                z = abs(z)
                coordinate_frame = "legacy_raw_fallback"

            forward = z
            if forward <= 1e-6:
                self._emit_direction_debug({
                    "status": "invalid",
                    "reason": "forward_component_too_small",
                    "timestamp": eye.get("timestamp"),
                    "raw_x": raw_x, "raw_y": raw_y, "raw_z": raw_z,
                    "camera_x": x, "camera_y": y, "camera_z": z,
                    "coordinate_frame": coordinate_frame,
                })
                return
            horizontal = math.degrees(math.atan2(x, forward))
            # PV camera +Y points down in the image. Keep that convention here:
            # positive vertical angle means DOWN, negative means UP.
            vertical = math.degrees(math.atan2(y, forward))
        except Exception as exc:
            self._emit_direction_debug({
                "status": "invalid",
                "reason": "direction_parse_error",
                "timestamp": eye.get("timestamp"),
                "error": str(exc),
            })
            return

        # The first valid fixation after direction feedback begins is the local
        # center.  All direction likelihoods are relative to this fixation rather
        # than the HoloLens optical-forward axis.
        if self._direction_center_horizontal is None:
            self._direction_center_horizontal = horizontal
            self._direction_center_vertical = vertical
            self._emit_direction_debug({
                "status": "center_set",
                "reason": "first_valid_direction_sample",
                "timestamp": eye.get("timestamp"),
                "raw_x": raw_x, "raw_y": raw_y, "raw_z": raw_z,
                "horizontal_deg": horizontal,
                "vertical_deg": vertical,
                "center_horizontal_deg": horizontal,
                "center_vertical_deg": vertical,
                "instant_direction": "center",
                "coordinate_frame": coordinate_frame,
                "camera_x": x, "camera_y": y, "camera_z": z,
            })
            return

        delta_h = horizontal - self._direction_center_horizontal
        delta_v = vertical - (self._direction_center_vertical or 0.0)

        ah = abs(delta_h)
        av = abs(delta_v)
        neutral = (
            ah <= self.direction_neutral_deg
            and av <= self.direction_neutral_deg
        )
        initial_label = self._hard_direction_label(delta_h, delta_v)
        likelihoods = self._sample_direction_likelihoods(delta_h, delta_v)
        instant_direction = initial_label or ("center" if neutral else "ambiguous")

        # When no directional evidence is active, neutral fixation can slowly
        # update the local center.  Once a direction attempt starts, neutral and
        # ambiguous samples are retained because they are evidence *against* a
        # confident command.
        if neutral and not self._direction_samples:
            self._direction_latched = False
            old_center_h = self._direction_center_horizontal
            old_center_v = self._direction_center_vertical or 0.0
            self._emit_direction_debug({
                "status": "valid",
                "reason": "neutral_center_adaptation",
                "timestamp": eye.get("timestamp"),
                "raw_x": raw_x, "raw_y": raw_y, "raw_z": raw_z,
                "horizontal_deg": horizontal, "vertical_deg": vertical,
                "center_horizontal_deg": old_center_h,
                "center_vertical_deg": old_center_v,
                "delta_horizontal_deg": delta_h, "delta_vertical_deg": delta_v,
                "instant_direction": instant_direction,
                "coordinate_frame": coordinate_frame,
                "camera_x": x, "camera_y": y, "camera_z": z,
                "prob_left": likelihoods["left"],
                "prob_right": likelihoods["right"],
                "prob_up": likelihoods["up"],
                "prob_down": likelihoods["down"],
                "prob_center": likelihoods["center"],
                "valid_samples": 0,
            })
            alpha = min(1.0, max(0.0, self.center_adaptation_alpha))
            self._direction_center_horizontal = (
                (1.0 - alpha) * self._direction_center_horizontal + alpha * horizontal
            )
            self._direction_center_vertical = (
                (1.0 - alpha) * (self._direction_center_vertical or 0.0) + alpha * vertical
            )
            return

        # A deliberate attempt starts only after a sample crosses the ordinary
        # direction threshold with a clear dominant axis.  This avoids filling the
        # likelihood window with idle center-looking samples before the participant
        # begins the gesture.
        if not self._direction_samples and initial_label is None:
            self._emit_direction_debug({
                "status": "valid",
                "reason": "below_threshold_or_ambiguous",
                "timestamp": eye.get("timestamp"),
                "raw_x": raw_x, "raw_y": raw_y, "raw_z": raw_z,
                "horizontal_deg": horizontal, "vertical_deg": vertical,
                "center_horizontal_deg": self._direction_center_horizontal,
                "center_vertical_deg": self._direction_center_vertical or 0.0,
                "delta_horizontal_deg": delta_h, "delta_vertical_deg": delta_v,
                "instant_direction": instant_direction,
                "coordinate_frame": coordinate_frame,
                "camera_x": x, "camera_y": y, "camera_z": z,
                "prob_left": likelihoods["left"],
                "prob_right": likelihoods["right"],
                "prob_up": likelihoods["up"],
                "prob_down": likelihoods["down"],
                "prob_center": likelihoods["center"],
                "valid_samples": 0,
            })
            return

        self._direction_samples.append(
            {
                "time": now,
                "likelihoods": likelihoods,
                "horizontal_deg": horizontal,
                "vertical_deg": vertical,
                "delta_horizontal_deg": delta_h,
                "delta_vertical_deg": delta_v,
            }
        )
        cutoff = now - max(0.05, self.direction_window_seconds)
        self._direction_samples = [
            sample for sample in self._direction_samples
            if float(sample["time"]) >= cutoff
        ]
        if not self._direction_samples:
            return

        labels = ("left", "right", "up", "down", "center")
        aggregated = {
            label: sum(float(s["likelihoods"][label]) for s in self._direction_samples)
            / len(self._direction_samples)
            for label in labels
        }
        direction_labels = ("left", "right", "up", "down")
        best = max(direction_labels, key=lambda label: aggregated[label])
        best_probability = aggregated[best]
        runner_up = max(
            aggregated[label] for label in labels if label != best
        )
        margin = best_probability - runner_up
        valid_count = len(self._direction_samples)

        self._emit_direction_debug({
            "status": "valid",
            "reason": "direction_evidence",
            "timestamp": eye.get("timestamp"),
            "raw_x": raw_x, "raw_y": raw_y, "raw_z": raw_z,
            "horizontal_deg": horizontal, "vertical_deg": vertical,
            "center_horizontal_deg": self._direction_center_horizontal,
            "center_vertical_deg": self._direction_center_vertical or 0.0,
            "delta_horizontal_deg": delta_h, "delta_vertical_deg": delta_v,
            "instant_direction": instant_direction,
            "coordinate_frame": coordinate_frame,
            "camera_x": x, "camera_y": y, "camera_z": z,
            "instant_prob_left": likelihoods["left"],
            "instant_prob_right": likelihoods["right"],
            "instant_prob_up": likelihoods["up"],
            "instant_prob_down": likelihoods["down"],
            "instant_prob_center": likelihoods["center"],
            "rolling_direction": best,
            "rolling_confidence": best_probability,
            "rolling_runner_up": runner_up,
            "rolling_margin": margin,
            "prob_left": aggregated["left"],
            "prob_right": aggregated["right"],
            "prob_up": aggregated["up"],
            "prob_down": aggregated["down"],
            "prob_center": aggregated["center"],
            "valid_samples": valid_count,
            "required_samples": self.direction_min_valid_samples,
        })

        # If a tentative look was followed by a clear return to center, close that
        # evidence attempt. This prevents an old weak glance from keeping the
        # direction window permanently active and allows normal center adaptation
        # to resume before the participant tries again.
        if (
            valid_count >= self.direction_min_valid_samples
            and aggregated["center"] >= max(0.75, self.direction_probability_threshold)
        ):
            self._direction_samples = []
            self._direction_last_status_emit = None
            return

        # Throttled progress signal for the participant feedback panel/log.
        if (
            self._direction_last_status_emit is None
            or now - self._direction_last_status_emit >= 0.20
        ):
            self._direction_last_status_emit = now
            self.gesture_observed.emit(
                {
                    "context": self._context,
                    "gesture": "direction_window_update",
                    "direction": best,
                    "confidence": best_probability,
                    "runner_up_confidence": runner_up,
                    "confidence_margin": margin,
                    "valid_samples": valid_count,
                    "required_samples": self.direction_min_valid_samples,
                    "window_seconds": self.direction_window_seconds,
                }
            )

        if self._direction_latched:
            return
        if valid_count < self.direction_min_valid_samples:
            return
        if best_probability < self.direction_probability_threshold:
            return
        if margin < self.direction_probability_margin:
            return

        latest = self._direction_samples[-1]
        payload = {
            "context": self._context,
            "gesture": "windowed_direction",
            "direction": best,
            "command": best,
            "confidence": best_probability,
            "runner_up_confidence": runner_up,
            "confidence_margin": margin,
            "valid_samples": valid_count,
            "window_seconds": self.direction_window_seconds,
            "prob_left": aggregated["left"],
            "prob_right": aggregated["right"],
            "prob_up": aggregated["up"],
            "prob_down": aggregated["down"],
            "prob_center": aggregated["center"],
            "horizontal_deg": latest["horizontal_deg"],
            "vertical_deg": latest["vertical_deg"],
            "delta_horizontal_deg": latest["delta_horizontal_deg"],
            "delta_vertical_deg": latest["delta_vertical_deg"],
        }
        self._direction_latched = True
        self._direction_samples = []
        self.gesture_observed.emit(dict(payload))
        self.command_recognized.emit(payload)

    def _hard_direction_label(self, delta_h: float, delta_v: float) -> str | None:
        """Return a clear threshold-crossing direction, or ``None``."""
        ah = abs(delta_h)
        av = abs(delta_v)
        margin = self.direction_dominance_margin_deg
        threshold = self.direction_threshold_deg
        if ah >= threshold and ah >= av + margin:
            return "right" if delta_h > 0 else "left"
        if av >= threshold and av >= ah + margin:
            return "down" if delta_v > 0 else "up"
        return None

    def _sample_direction_likelihoods(
        self, delta_h: float, delta_v: float
    ) -> dict[str, float]:
        """Return normalized likelihoods for L/R/U/D/center for one sample.

        Each class is represented by a simple angular prototype around the local
        fixation.  A Gaussian radial likelihood gives stronger weight to a clear,
        sharp eye movement while naturally splitting probability for diagonal or
        near-center samples.  This is intentionally lightweight and deterministic
        rather than a learned participant-specific model.
        """
        threshold = max(1.0, self.direction_threshold_deg)
        prototype = 1.5 * threshold
        sigma = max(3.0, 0.5 * threshold)
        prototypes = {
            "left": (-prototype, 0.0),
            "right": (prototype, 0.0),
            "up": (0.0, -prototype),
            "down": (0.0, prototype),
            "center": (0.0, 0.0),
        }
        weights: dict[str, float] = {}
        denom = 2.0 * sigma * sigma
        for label, (ph, pv) in prototypes.items():
            distance_sq = (delta_h - ph) ** 2 + (delta_v - pv) ** 2
            weights[label] = math.exp(-distance_sq / denom)
        total = sum(weights.values())
        if total <= 1e-12:
            return {label: 0.2 for label in prototypes}
        return {label: value / total for label, value in weights.items()}

