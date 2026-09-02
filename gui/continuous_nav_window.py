"""Participant-facing window for Study 1(b) continuous room navigation."""

from __future__ import annotations

import math
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QKeyEvent, QPainter, QPen, QPolygonF
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from models.enums import Environment, FeedbackTiming, Modality


class ContinuousNavCanvas(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(680, 680)
        self._world: dict = {}
        self._state: dict = {}

    def set_world(self, world: dict | None) -> None:
        self._world = dict(world or {})
        self.update()

    def set_state(self, state: dict | None) -> None:
        self._state = dict(state or {})
        self.update()

    def _bounds(self) -> tuple[float, float, float, float]:
        bounds = self._world.get("bounds") or {}
        min_x = float(bounds.get("min_x", 0.0))
        max_x = float(bounds.get("max_x", 12.0))
        min_y = float(bounds.get("min_y", 0.0))
        max_y = float(bounds.get("max_y", 12.0))
        if max_x <= min_x:
            max_x = min_x + 1.0
        if max_y <= min_y:
            max_y = min_y + 1.0
        return min_x, max_x, min_y, max_y

    def _transform(self, x: float, y: float) -> QPointF:
        min_x, max_x, min_y, max_y = self._bounds()
        margin = 28.0
        usable_w = max(1.0, self.width() - 2 * margin)
        usable_h = max(1.0, self.height() - 2 * margin)
        scale = min(usable_w / (max_x - min_x), usable_h / (max_y - min_y))
        draw_w = (max_x - min_x) * scale
        draw_h = (max_y - min_y) * scale
        ox = (self.width() - draw_w) / 2.0
        oy = (self.height() - draw_h) / 2.0
        # Simulation coordinates are Cartesian (positive Y up); Qt Y points down.
        sx = ox + (x - min_x) * scale
        sy = oy + draw_h - (y - min_y) * scale
        return QPointF(sx, sy)

    def _scale(self) -> float:
        min_x, max_x, min_y, max_y = self._bounds()
        margin = 28.0
        return min(
            max(1.0, self.width() - 2 * margin) / (max_x - min_x),
            max(1.0, self.height() - 2 * margin) / (max_y - min_y),
        )

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor("white"))

        if not self._world:
            painter.setPen(QColor("#666"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Waiting for Ubuntu room geometry…")
            return

        scale = self._scale()
        painter.setPen(QPen(QColor("#222"), 3))
        for line in self._world.get("lines", []):
            p1 = self._transform(float(line["x1"]), float(line["y1"]))
            p2 = self._transform(float(line["x2"]), float(line["y2"]))
            painter.drawLine(p1, p2)

        painter.setPen(QPen(QColor("#555"), 2))
        painter.setBrush(QColor("#d9d9d9"))
        for circle in self._world.get("circles", []):
            center = self._transform(float(circle["x"]), float(circle["y"]))
            radius = float(circle["radius"]) * scale
            painter.drawEllipse(center, radius, radius)

        # Current target supplied by Ubuntu for the active episode.
        gx = self._state.get("goal_x")
        gy = self._state.get("goal_y")
        if gx is not None and gy is not None:
            goal = self._transform(float(gx), float(gy))
            radius = max(9.0, float(self._state.get("goal_radius") or 0.35) * scale)
            painter.setPen(QPen(QColor("#9f1d20"), 2))
            painter.setBrush(QColor("#ef6a6a"))
            painter.drawEllipse(goal, radius, radius)

        rx = self._state.get("robot_x")
        ry = self._state.get("robot_y")
        if rx is None or ry is None:
            return

        center = self._transform(float(rx), float(ry))
        orientation = float(self._state.get("robot_orientation") or 0.0)
        # Draw a heading triangle. Positive simulator angle is assumed CCW.
        robot_len = max(18.0, 0.42 * scale)
        robot_w = max(12.0, 0.28 * scale)

        def point(forward: float, lateral: float) -> QPointF:
            wx = float(rx) + forward * math.cos(orientation) - lateral * math.sin(orientation)
            wy = float(ry) + forward * math.sin(orientation) + lateral * math.cos(orientation)
            return self._transform(wx, wy)

        nose = point(robot_len / scale, 0.0)
        back_left = point(-0.45 * robot_len / scale, robot_w / scale)
        back_right = point(-0.45 * robot_len / scale, -robot_w / scale)
        painter.setPen(QPen(QColor("#154c79"), 2))
        painter.setBrush(QColor("#2d7dd2"))
        painter.drawPolygon(QPolygonF([nose, back_left, back_right]))
        painter.setBrush(QColor("#154c79"))
        painter.drawEllipse(center, 3.5, 3.5)


class ContinuousNavParticipantWindow(QWidget):
    """Live room view and lock-step human feedback input."""

    def __init__(self, controller, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._controller = controller
        self._client = controller.continuous_nav_client
        self._waiting_request: dict | None = None
        self._last_state: dict = {}
        self._feedback_deadline_monotonic = 0.0
        self._joystick_last_sent_request = ""

        self.setWindowTitle("HINT — Study 1(b) Continuous Room Navigation")
        self.resize(920, 900)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        root = QVBoxLayout(self)
        self._title = QLabel("Study 1(b) — Continuous Action-Space Room Navigation")
        self._title.setStyleSheet("font-size: 19px; font-weight: bold;")
        root.addWidget(self._title)

        self._status = QLabel("Waiting for the Ubuntu navigation worker…")
        self._status.setWordWrap(True)
        self._status.setStyleSheet("font-size: 14px;")
        root.addWidget(self._status)

        self._canvas = ContinuousNavCanvas()
        root.addWidget(self._canvas, 1)

        self._control_hint = QLabel(
            "The RL agent is controlling the robot. When a collision is detected, "
            "the simulator rewinds and this window will request human correction."
        )
        self._control_hint.setWordWrap(True)
        self._control_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._control_hint.setStyleSheet("font-size: 14px; padding: 8px;")
        root.addWidget(self._control_hint)

        buttons = QHBoxLayout()
        self._action_buttons = []
        for text, action in [
            ("Sharp Left", 0),
            ("Left", 1),
            ("Slight Left", 2),
            ("Straight", 3),
            ("Slight Right", 4),
            ("Right", 5),
            ("Sharp Right", 6),
        ]:
            button = QPushButton(text)
            button.clicked.connect(lambda _checked=False, a=action: self._submit_action(a, "button"))
            self._action_buttons.append(button)
            buttons.addWidget(button)
        root.addLayout(buttons)

        self._anytime_btn = QPushButton("INTERVENE NOW  [SPACE]")
        self._anytime_btn.clicked.connect(self._begin_anytime_feedback)
        self._anytime_btn.setVisible(False)
        root.addWidget(self._anytime_btn)

        self._skip_btn = QPushButton("Skip / No Feedback")
        self._skip_btn.clicked.connect(lambda: self._submit_action(None, "skip_button"))
        root.addWidget(self._skip_btn)

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(50)
        self._poll_timer.timeout.connect(self._poll_runtime_input)
        self._poll_timer.start()

        self._client.trial_prepared.connect(self._on_trial_prepared)
        self._client.task_started.connect(self._on_task_started)
        self._client.task_ended.connect(self._on_task_ended)
        self._client.remote_error.connect(self._on_remote_error)
        self._client.state_updated.connect(self._on_state_update)
        self._client.collision.connect(self._on_collision)
        self._client.human_action_requested.connect(self._on_action_request)
        self._client.human_action_applied.connect(self._on_action_applied)
        self._client.message_received.connect(self._on_remote_message)

        self._set_feedback_controls_enabled(False)

    # ------------------------------------------------------------------
    def _room_trial_active(self) -> bool:
        trial = self._controller.active_trial
        return bool(
            trial is not None
            and trial.condition.environment == Environment.CONTINUOUS_ROOM
        )

    def _current_modality(self) -> Modality | None:
        trial = self._controller.active_trial
        if trial is None:
            return None
        return trial.condition.modality

    def _current_timing(self) -> FeedbackTiming | None:
        trial = self._controller.active_trial
        if trial is None:
            return None
        return trial.condition.feedback_timing

    def _set_feedback_controls_enabled(self, enabled: bool) -> None:
        # Mouse steering buttons are convenient during development, but Study
        # mode must preserve the selected feedback modality (Keyboard/Joystick).
        allow_mouse_actions = enabled and self._controller.config.mode.value == "DEVELOPMENT"
        for button in getattr(self, "_action_buttons", []):
            button.setEnabled(allow_mouse_actions)
        if hasattr(self, "_skip_btn"):
            self._skip_btn.setEnabled(enabled)

    def _on_trial_prepared(self, msg: dict) -> None:
        self._canvas.set_world(msg.get("world") or {})
        self._status.setText("Ubuntu room is prepared. Waiting for task start…")

    def _on_task_started(self, msg: dict) -> None:
        if not self._room_trial_active():
            return
        self._status.setText("Agent training is running on the Ubuntu PC.")
        self._waiting_request = None
        self._set_feedback_controls_enabled(False)
        anytime = self._current_timing() == FeedbackTiming.ANYTIME and self._current_modality() == Modality.KEYBOARD
        self._anytime_btn.setVisible(anytime)
        self._anytime_btn.setEnabled(anytime)
        if anytime:
            self._control_hint.setText("Keyboard Anytime mode: press SPACE or INTERVENE NOW whenever you want to provide corrective feedback.")
        elif self._current_modality() == Modality.NONE:
            self._control_hint.setText("Observation mode: watch the RL agent learn. No participant feedback is accepted.")
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus()

    def _on_task_ended(self, msg: dict) -> None:
        if not self.isVisible() and not self._room_trial_active():
            return
        self._waiting_request = None
        self._set_feedback_controls_enabled(False)
        self._status.setText(
            f"Ubuntu navigation task stopped ({msg.get('status') or msg.get('type')})."
        )
        self._control_hint.setText("The researcher can now mark the run valid, invalid, or aborted in HINT Console.")

    def _on_remote_error(self, message: str) -> None:
        if not self._room_trial_active():
            return
        self._waiting_request = None
        self._set_feedback_controls_enabled(False)
        first_line = str(message).splitlines()[0] if str(message).splitlines() else str(message)
        self._status.setText(f"Ubuntu RL failed: {first_line}")
        self._control_hint.setText("The RL process stopped. The researcher should mark this run Invalid/Repeat and inspect the failure log.")

    def _on_state_update(self, msg: dict) -> None:
        self._last_state = dict(msg)
        self._canvas.set_state(msg)
        phase = str(msg.get("phase") or "RL").upper()
        if phase == "RL" and self._waiting_request is None:
            self._status.setText(
                f"RL control — Episode {msg.get('episode', '--')} | Step {msg.get('step', '--')}"
            )

    def _on_collision(self, msg: dict) -> None:
        if self._room_trial_active():
            self._status.setText("Collision detected — simulator is rewinding to the correction state…")
            self._control_hint.setText("Collision detected. Wait for the restored state before giving feedback.")

    def _on_action_request(self, msg: dict) -> None:
        if not self._room_trial_active():
            return
        if self._current_modality() == Modality.NONE:
            # Defensive fallback: observation trials must never collect human feedback.
            try:
                self._controller.send_continuous_nav_action(str(msg.get("request_id") or ""), None, source_detail="observation_auto_skip")
            except Exception:
                pass
            self._status.setText("Observation mode — human feedback request ignored; agent continues without participant input.")
            return
        self._waiting_request = dict(msg)
        self._anytime_btn.setEnabled(False)
        timeout_s = float(
            getattr(self._controller, "_continuous_nav_feedback_timeout_seconds", 10.0)
        )
        self._feedback_deadline_monotonic = time.monotonic() + timeout_s
        self._set_feedback_controls_enabled(True)
        self._status.setText(
            f"HUMAN CONTROL — correction step {msg.get('human_step', '--')} / "
            f"{msg.get('human_total_steps', '--')}"
        )
        modality = self._current_modality()
        if modality == Modality.JOYSTICK:
            hint = (
                "Use the connected joystick. Push left/right for steering; push forward for straight. "
                "One command is sent for each displayed correction state."
            )
        else:
            hint = (
                "Keyboard: W/S = straight, A/D = medium turn, Q/E = slight turn, "
                "Shift+A/Shift+D = sharp turn. Esc skips this intervention."
            )
        self._control_hint.setText(hint)
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus()

    def _on_action_applied(self, msg: dict) -> None:
        # The next STATE_UPDATE and HUMAN_ACTION_REQUEST define the next lock-step state.
        if self._room_trial_active():
            self._status.setText(
                f"Applied human action at correction step {msg.get('human_step', '--')}. Updating state…"
            )

    def _on_remote_message(self, msg: dict) -> None:
        kind = str(msg.get("type") or "").upper()
        if kind in {"STATE_RESTORED", "HUMAN_CONTROL_STARTED"} and self._room_trial_active():
            self._status.setText("Rewind complete — human correction is starting.")
        elif kind == "RL_CONTROL_RESUMED" and self._room_trial_active():
            self._waiting_request = None
            self._set_feedback_controls_enabled(False)
            self._status.setText("Human correction finished — RL control resumed from the final corrected state.")
            anytime = self._current_timing() == FeedbackTiming.ANYTIME and self._current_modality() == Modality.KEYBOARD
            self._anytime_btn.setEnabled(anytime)
            self._control_hint.setText("Press SPACE to intervene again." if anytime else "The RL agent is controlling the robot.")
        elif kind == "HUMAN_ACTION_TIMEOUT" and self._room_trial_active():
            self._waiting_request = None
            self._set_feedback_controls_enabled(False)
            self._status.setText("Human-feedback timeout — RL will continue from the restored state.")

    # ------------------------------------------------------------------
    def keyPressEvent(self, event: QKeyEvent) -> None:
        if self._waiting_request is None:
            if (event.key() == Qt.Key.Key_Space and self._current_modality() == Modality.KEYBOARD
                    and self._current_timing() == FeedbackTiming.ANYTIME):
                self._begin_anytime_feedback()
                event.accept()
                return
            super().keyPressEvent(event)
            return
        if self._current_modality() != Modality.KEYBOARD:
            super().keyPressEvent(event)
            return
        key = event.key()
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        action = None
        detail = "keyboard"
        handled = True
        if key in (Qt.Key.Key_W, Qt.Key.Key_S):
            action = 3
        elif key == Qt.Key.Key_A:
            action = 0 if shift else 1
        elif key == Qt.Key.Key_D:
            action = 6 if shift else 5
        elif key == Qt.Key.Key_Q:
            action = 2
        elif key == Qt.Key.Key_E:
            action = 4
        elif key == Qt.Key.Key_Escape:
            action = None
            detail = "keyboard_escape_skip"
        else:
            handled = False
        if handled:
            self._submit_action(action, detail)
            event.accept()
            return
        super().keyPressEvent(event)

    def _begin_anytime_feedback(self) -> None:
        if self._waiting_request is not None:
            return
        if self._current_timing() != FeedbackTiming.ANYTIME or self._current_modality() != Modality.KEYBOARD:
            return
        try:
            self._controller.begin_continuous_anytime_feedback()
            self._anytime_btn.setEnabled(False)
            self._status.setText("Anytime intervention requested — waiting for Ubuntu worker to expose the correction state…")
        except Exception as exc:
            self._status.setText(f"Could not start Anytime feedback: {exc}")

    def _poll_runtime_input(self) -> None:
        if self._waiting_request is None:
            return
        if self._feedback_deadline_monotonic and time.monotonic() >= self._feedback_deadline_monotonic:
            self._set_feedback_controls_enabled(False)
            return
        if self._current_modality() != Modality.JOYSTICK:
            return

        stats = self._controller.device_manager.joystick_stats()
        axes = list(stats.get("axes") or [])
        if not axes:
            return
        x = float(axes[0])
        y = float(axes[1]) if len(axes) > 1 else 0.0
        request_id = str(self._waiting_request.get("request_id") or "")
        if not request_id or request_id == self._joystick_last_sent_request:
            return

        action: int | None = None
        if x <= -0.75:
            action = 0
        elif x <= -0.45:
            action = 1
        elif x <= -0.15:
            action = 2
        elif x >= 0.75:
            action = 6
        elif x >= 0.45:
            action = 5
        elif x >= 0.15:
            action = 4
        elif y <= -0.35:
            action = 3

        if action is not None:
            self._joystick_last_sent_request = request_id
            self._submit_action(action, f"joystick_axes:x={x:.3f},y={y:.3f}")

    def _submit_action(self, action: int | None, source_detail: str) -> None:
        request = self._waiting_request
        if request is None:
            return
        self._waiting_request = None
        self._set_feedback_controls_enabled(False)
        try:
            self._controller.send_continuous_nav_action(
                str(request.get("request_id") or ""),
                action,
                source_detail=source_detail,
            )
            label = "SKIP" if action is None else str(action)
            self._status.setText(f"Feedback sent (action {label}). Waiting for Ubuntu to apply it…")
        except Exception as exc:
            self._status.setText(f"Could not send feedback: {exc}")
