from __future__ import annotations

from PySide6.QtCore import (
    Qt,
    QTimer,
)

from PySide6.QtGui import (
    QColor,
    QImage,
    QKeyEvent,
    QPainter,
    QPen,
    QPixmap,
)

from PySide6.QtWidgets import (
    QApplication,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.application_controller import (
    ApplicationController,
)

from devices.voice_recognizer import (
    VOICE_CONTEXT_DIRECTION,
    VOICE_CONTEXT_IDLE,
    VOICE_CONTEXT_STATE_NUMBER,
    VOICE_CONTEXT_STOP,
    VoiceCommandRecognizer,
)
from devices.gaze_gesture_recognizer import (
    GAZE_CONTEXT_BLINK_COUNT,
    GAZE_CONTEXT_DIRECTION,
    GAZE_CONTEXT_DOUBLE_BLINK,
    GAZE_CONTEXT_IDLE,
    GAZE_CONTEXT_LONG_CLOSE,
    EyeGazeGestureRecognizer,
)
from models.enums import (
    AppMode,
    Environment,
    EventType,
    FeedbackTiming,
    Modality,
)
from models.event import StudyEvent
from gui.participant_start_overlay import ParticipantStartOverlay


class MazeCanvas(QWidget):

    def __init__(
        self,
        parent=None,
    ) -> None:

        super().__init__(parent)

        self.setMinimumSize(
            600,
            600,
        )

        self.maze = None

        self.agent_position = None

        self.target_position = None

        self.ambiguous_position = None

        self.history_positions = []

        self.selected_history_step = None

    def set_state(
        self,
        payload: dict,
    ) -> None:

        self.maze = payload.get(
            "maze"
        )

        self.agent_position = (
            payload.get(
                "agent_position"
            )
        )

        self.target_position = (
            payload.get(
                "target_position"
            )
        )

        self.ambiguous_position = (
            payload.get(
                "ambiguous_position"
            )
        )

        self.update()

    def set_history(
        self,
        history: list[dict],
        selected_step=None,
    ) -> None:

        self.history_positions = list(
            history
        )

        self.selected_history_step = (
            selected_step
        )

        self.update()

    def clear_history(
        self,
    ) -> None:

        self.history_positions = []
        self.selected_history_step = None
        self.update()

    def paintEvent(
        self,
        event,
    ) -> None:

        super().paintEvent(event)

        if self.maze is None:
            return

        painter = QPainter(self)

        rows, cols = (
            self.maze.shape
        )

        cell = min(
            self.width() / cols,
            self.height() / rows,
        )

        x_offset = (
            self.width()
            - cell * cols
        ) / 2

        y_offset = (
            self.height()
            - cell * rows
        ) / 2

        painter.setPen(
            QPen(
                QColor("#c8c8c8"),
                1,
            )
        )

        for r in range(rows):

            for c in range(cols):

                x = (
                    x_offset
                    + c * cell
                )

                y = (
                    y_offset
                    + r * cell
                )

                if self.maze[r, c] == 1:

                    painter.fillRect(
                        int(x),
                        int(y),
                        int(cell),
                        int(cell),
                        QColor("#333333"),
                    )

                elif (
                    self.ambiguous_position
                    == (r, c)
                ):

                    painter.fillRect(
                        int(x),
                        int(y),
                        int(cell),
                        int(cell),
                        QColor("#90EE90"),
                    )

                else:

                    painter.fillRect(
                        int(x),
                        int(y),
                        int(cell),
                        int(cell),
                        QColor("white"),
                    )

                painter.drawRect(
                    int(x),
                    int(y),
                    int(cell),
                    int(cell),
                )

                # Goal
                if (
                    self.target_position
                    == (r, c)
                ):

                    margin = (
                        cell * 0.22
                    )

                    painter.setBrush(
                        QColor("#e74c3c")
                    )

                    painter.drawEllipse(
                        int(x + margin),
                        int(y + margin),

                        int(
                            cell
                            - 2 * margin
                        ),

                        int(
                            cell
                            - 2 * margin
                        ),
                    )

                    painter.setBrush(
                        Qt.BrushStyle.NoBrush
                    )

                # Agent
                if (
                    self.agent_position
                    == (r, c)
                ):

                    margin = (
                        cell * 0.22
                    )

                    painter.fillRect(
                        int(x + margin),
                        int(y + margin),

                        int(
                            cell
                            - 2 * margin
                        ),

                        int(
                            cell
                            - 2 * margin
                        ),

                        QColor("#2d7dd2"),
                    )


        # Recent-state markers for Anytime feedback selection.
        # The numbered marker corresponds to the state-selection
        # button shown below the maze.
        for item in self.history_positions:

            state = item.get("state")

            if state is None:
                continue

            r, c = state

            x = x_offset + c * cell
            y = y_offset + r * cell

            marker_size = max(
                18.0,
                cell * 0.28,
            )

            marker_x = (
                x + cell * 0.06
            )

            marker_y = (
                y + cell * 0.06
            )

            is_selected = (
                item.get("step")
                == self.selected_history_step
            )

            painter.setBrush(
                QColor(
                    "#2a9d8f"
                    if is_selected
                    else "#f4a261"
                )
            )

            painter.setPen(
                QPen(
                    QColor("#202020"),
                    1,
                )
            )

            painter.drawEllipse(
                int(marker_x),
                int(marker_y),
                int(marker_size),
                int(marker_size),
            )

            painter.setPen(
                QColor("#101010")
            )

            painter.drawText(
                int(
                    marker_x
                    + marker_size * 0.30
                ),
                int(
                    marker_y
                    + marker_size * 0.72
                ),
                str(
                    item.get(
                        "history_index",
                        "",
                    )
                ),
            )


class ParticipantWindow(QWidget):

    def __init__(
        self,
        controller:
            ApplicationController,
    ) -> None:

        super().__init__()

        self._controller = controller

        self._feedback_timing = (
            FeedbackTiming.REQUESTED
        )

        self._feedback_modality = Modality.KEYBOARD

        self._waiting_for_feedback = (
            False
        )

        self._remaining_seconds = 0

        self._anytime_feedback_active = False

        self._anytime_history = []

        self._selected_history_step = None

        self._live_state_payload = None

        self._history_buttons = []

        # Joystick feedback is polled from the already-selected device in the
        # researcher console.  A small latch prevents a held stick/button from
        # being submitted repeatedly before it returns to neutral.
        self._joystick_axis_latched = False
        self._joystick_button_latched = False
        self._joystick_history_cursor = 0

        # Live HoloLens PV + projected gaze preview.  This reads snapshots
        # from the already-running device connection; it never opens a second
        # camera/EET stream.  The timer is only active while an Eye Gaze
        # feedback interaction is on screen.
        self._gaze_preview_last_pixmap: QPixmap | None = None
        self._gaze_preview_smoothed: tuple[float, float] | None = None

        voice_cfg = (
            self._controller.config.study_raw
            .get("voice_recognition", {})
        )
        self._voice_recognizer = VoiceCommandRecognizer(
            self._controller.device_manager.microphone_device,
            config=voice_cfg,
            parent=self,
        )
        self._voice_recognizer.transcript_heard.connect(
            self._on_voice_transcript
        )
        self._voice_recognizer.command_recognized.connect(
            self._on_voice_command
        )
        self._voice_recognizer.recognition_error.connect(
            self._on_voice_error
        )

        gaze_cfg = (
            self._controller.config.study_raw
            .get("eye_gaze_recognition", {})
        )
        self._gaze_recognizer = EyeGazeGestureRecognizer(
            self._controller.device_manager.hololens_device,
            config=gaze_cfg,
            parent=self,
        )
        self._gaze_recognizer.gesture_observed.connect(
            self._on_gaze_gesture
        )
        self._gaze_recognizer.command_recognized.connect(
            self._on_gaze_command
        )
        self._gaze_recognizer.direction_debug.connect(
            self._on_gaze_direction_debug
        )
        self._gaze_recognizer.recognition_error.connect(
            self._on_gaze_error
        )

        self.setWindowTitle(
            "HINT Study — Participant"
        )

        self.setFocusPolicy(
            Qt.FocusPolicy.StrongFocus
        )

        root = QVBoxLayout(self)

        self._status_label = QLabel(
            "Waiting for trial..."
        )

        self._status_label.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )

        self._status_label.setStyleSheet(
            "font-size: 20px; "
            "font-weight: bold;"
        )

        root.addWidget(
            self._status_label
        )

        self._condition_label = QLabel(
            "Condition: --"
        )
        self._condition_label.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )
        self._condition_label.setStyleSheet(
            "font-size: 16px; font-weight: bold; color: #555;"
        )
        root.addWidget(self._condition_label)

        self._maze = MazeCanvas()

        root.addWidget(
            self._maze,
            1,
        )

        self._pause_feedback_btn = QPushButton(
            "PAUSE & SELECT FEEDBACK  [SPACE]"
        )

        self._pause_feedback_btn.setStyleSheet(
            "font-size: 18px; "
            "font-weight: bold; "
            "padding: 10px;"
        )

        self._pause_feedback_btn.setFocusPolicy(
            Qt.FocusPolicy.NoFocus
        )

        root.addWidget(
            self._pause_feedback_btn
        )

        self._history_box = QGroupBox(
            "Choose one of the recent states"
        )

        self._history_layout = QGridLayout(
            self._history_box
        )

        root.addWidget(
            self._history_box
        )

        self._history_box.setVisible(
            False
        )

        self._feedback_box = (
            QGroupBox(
                "Human Feedback"
            )
        )

        feedback_layout = QVBoxLayout(
            self._feedback_box
        )

        self._feedback_message = QLabel(
            "Use the controls when "
            "feedback is requested."
        )

        self._feedback_message.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )

        self._feedback_message.setStyleSheet(
            "font-size: 18px;"
        )

        feedback_layout.addWidget(
            self._feedback_message
        )

        grid = QGridLayout()

        self._up_btn = QPushButton(
            "↑  UP"
        )

        self._left_btn = QPushButton(
            "←  LEFT"
        )

        self._right_btn = QPushButton(
            "RIGHT  →"
        )

        self._down_btn = QPushButton(
            "↓  DOWN"
        )

        self._skip_btn = QPushButton(
            "SKIP"
        )

        grid.addWidget(
            self._up_btn,
            0,
            1,
        )

        grid.addWidget(
            self._left_btn,
            1,
            0,
        )

        grid.addWidget(
            self._right_btn,
            1,
            2,
        )

        grid.addWidget(
            self._down_btn,
            2,
            1,
        )

        grid.addWidget(
            self._skip_btn,
            3,
            1,
        )

        feedback_body = QHBoxLayout()
        feedback_body.addLayout(grid, 2)

        self._gaze_preview_box = QGroupBox(
            "HoloLens Camera + Eye Gaze"
        )
        gaze_preview_layout = QVBoxLayout(
            self._gaze_preview_box
        )

        self._gaze_preview_camera = QLabel(
            "Camera preview activates during Eye Gaze feedback."
        )
        self._gaze_preview_camera.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )
        self._gaze_preview_camera.setMinimumSize(360, 203)
        self._gaze_preview_camera.setStyleSheet(
            "background: #111; color: #ddd; border: 1px solid #444;"
        )
        gaze_preview_layout.addWidget(
            self._gaze_preview_camera,
            1,
        )

        self._gaze_preview_status = QLabel(
            "Preview inactive"
        )
        self._gaze_preview_status.setWordWrap(True)
        self._gaze_preview_status.setStyleSheet(
            "font-size: 11px; color: #666;"
        )
        gaze_preview_layout.addWidget(
            self._gaze_preview_status
        )

        self._gaze_direction_debug = QLabel(
            "Direction debug: waiting for gaze direction recognition…"
        )
        self._gaze_direction_debug.setWordWrap(True)
        self._gaze_direction_debug.setStyleSheet(
            "font-size: 12px; font-family: Consolas, monospace; "
            "background: #f6f6f6; border: 1px solid #bbb; padding: 5px;"
        )
        gaze_preview_layout.addWidget(self._gaze_direction_debug)

        self._gaze_preview_box.setVisible(False)
        feedback_body.addWidget(
            self._gaze_preview_box,
            3,
        )

        feedback_layout.addLayout(
            feedback_body
        )

        root.addWidget(
            self._feedback_box
        )

        self._up_btn.clicked.connect(
            lambda:
                self._send_action(0)
        )

        self._down_btn.clicked.connect(
            lambda:
                self._send_action(1)
        )

        self._left_btn.clicked.connect(
            lambda:
                self._send_action(2)
        )

        self._right_btn.clicked.connect(
            lambda:
                self._send_action(3)
        )

        self._skip_btn.clicked.connect(
            self._skip
        )

        self._pause_feedback_btn.clicked.connect(
            self._begin_anytime_feedback
        )

        self._countdown = QTimer(
            self
        )

        self._countdown.timeout.connect(
            self._tick_countdown
        )

        self._gaze_preview_timer = QTimer(self)
        self._gaze_preview_timer.setInterval(50)
        self._gaze_preview_timer.timeout.connect(
            self._refresh_gaze_preview
        )

        self._joystick_timer = QTimer(self)
        self._joystick_timer.setInterval(50)
        self._joystick_timer.timeout.connect(self._poll_joystick_feedback)
        self._joystick_timer.start()

        rl = (
            self._controller
            .rl_manager
        )

        rl.trial_started.connect(
            self._on_trial_started
        )

        rl.state_updated.connect(
            self._on_state_updated
        )

        rl.feedback_requested.connect(
            self._on_feedback_requested
        )

        rl.feedback_resolved.connect(
            self._on_feedback_resolved
        )

        rl.anytime_feedback_started.connect(
            self._on_anytime_feedback_started
        )

        rl.status_changed.connect(
            self._on_status_changed
        )

        self._set_controls(
            False
        )

        self._start_overlay = ParticipantStartOverlay(self)
        self._start_overlay.start_requested.connect(
            self._on_participant_start_requested
        )
        self._controller.event_bus.event_published.connect(
            self._on_activity_lifecycle_event
        )

    # --------------------------------------------------

    def _on_activity_lifecycle_event(self, event: StudyEvent) -> None:
        if event.event_type == EventType.ACTIVITY_PREPARED:
            trial = self._controller.active_trial
            if (
                trial is None
                or trial.trial_id != event.trial_id
                or trial.condition.environment == Environment.CONTINUOUS_ROOM
            ):
                return

            self._status_label.setText("Activity ready — waiting for you to start")
            if self._controller.config.mode == AppMode.STUDY:
                self.showFullScreen()
            else:
                self.resize(850, 900)
                self.show()
            self.raise_()
            self.activateWindow()
            self._start_overlay.present(trial)
            return

        if event.event_type == EventType.PARTICIPANT_ACTIVITY_STARTED:
            self._start_overlay.start_succeeded(event.trial_id or "")
        elif event.event_type == EventType.TRIAL_ENDED:
            gate_was_visible = self._start_overlay.isVisible()
            self._start_overlay.dismiss(event.trial_id or "")
            if gate_was_visible:
                self.hide()

    def _on_participant_start_requested(self, trial_id: str) -> None:
        try:
            self._controller.start_prepared_activity(trial_id)
        except Exception as exc:
            self._start_overlay.start_failed(
                str(exc),
                can_retry=not self._controller.activity_started,
            )

    def _on_trial_started(
        self,
        trial,
    ) -> None:

        self._feedback_timing = (
            trial.condition
            .feedback_timing
        )

        self._feedback_modality = trial.condition.modality
        self._condition_label.setText(
            f"Condition: {self._feedback_timing.value}  |  "
            f"{self._feedback_modality.value}"
        )

        self._waiting_for_feedback = (
            False
        )

        self._anytime_feedback_active = False
        self._anytime_history = []
        self._selected_history_step = None
        self._joystick_axis_latched = False
        self._joystick_button_latched = False
        self._joystick_history_cursor = 0
        self._live_state_payload = None
        self._clear_history_buttons()
        self._maze.clear_history()
        self._history_box.setVisible(False)
        self._voice_recognizer.set_context(VOICE_CONTEXT_IDLE)
        self._gaze_recognizer.set_context(GAZE_CONTEXT_IDLE)
        self._set_gaze_preview_active(False)
        self._pause_feedback_btn.setText(
            "PAUSE & SELECT FEEDBACK  [SPACE]"
        )

        if self._feedback_modality == Modality.NONE:
            self._feedback_message.setText(
                "Observation phase — watch the agent learn. No human feedback is required."
            )
            self._set_controls(False)
            self._skip_btn.setVisible(False)
            self._pause_feedback_btn.setVisible(False)
        elif (
            self._feedback_timing
            == FeedbackTiming.ANYTIME
        ):

            if self._feedback_modality == Modality.VOICE:
                if self._voice_recognizer.available:
                    self._feedback_message.setText(
                        'Voice feedback ready. Say "STOP" when you want '
                        "to pause the agent and choose a recent state."
                    )
                    self._voice_recognizer.set_context(VOICE_CONTEXT_STOP)
                else:
                    self._feedback_message.setText(
                        "Voice recognition is unavailable. Install the project "
                        "requirements before running this condition."
                    )
                self._pause_feedback_btn.setText(
                    'VOICE MODE — SAY "STOP" TO PAUSE'
                )
                self._pause_feedback_btn.setEnabled(False)
            elif self._feedback_modality == Modality.JOYSTICK:
                self._feedback_message.setText(
                    "Joystick feedback ready. Press the first joystick button when "
                    "you want to pause. Use LEFT/RIGHT to choose a recent state, "
                    "press the first button to confirm it, then tilt the stick in "
                    "the corrective direction."
                )
                self._pause_feedback_btn.setText(
                    "JOYSTICK MODE — PRESS BUTTON 1 TO PAUSE"
                )
                self._pause_feedback_btn.setEnabled(False)
            elif self._is_gaze_modality():
                self._feedback_message.setText(
                    "Eye-gaze feedback ready. BLINK TWICE to pause the agent. "
                    "Keep your head generally forward so the gaze gesture is "
                    "measured relative to the headset."
                )
                self._gaze_recognizer.set_context(GAZE_CONTEXT_DOUBLE_BLINK)
                self._pause_feedback_btn.setText(
                    "EYE GAZE MODE — BLINK TWICE TO PAUSE"
                )
                self._pause_feedback_btn.setEnabled(False)
            else:
                self._feedback_message.setText(
                    "When you want to give feedback, "
                    "press SPACE or the Pause button. "
                    "Then choose a recent state and "
                    "provide the corrective action."
                )
                self._pause_feedback_btn.setEnabled(True)

            # Direction controls are only enabled after
            # the participant explicitly selects a state.
            self._set_controls(False)

            self._skip_btn.setVisible(False)
            self._pause_feedback_btn.setVisible(True)

        else:

            if self._feedback_modality == Modality.VOICE:
                self._feedback_message.setText(
                    "Wait until the system requests feedback. Then say "
                    "UP, DOWN, LEFT, or RIGHT."
                )
                self._skip_btn.setVisible(False)
            elif self._feedback_modality == Modality.JOYSTICK:
                self._feedback_message.setText(
                    "Wait until the system requests feedback. Then tilt the "
                    "joystick UP, DOWN, LEFT, or RIGHT."
                )
                self._skip_btn.setVisible(False)
            elif self._is_gaze_modality():
                self._feedback_message.setText(
                    "Wait until the system requests feedback. First look normally at the "
                    "agent/maze so the local gaze center can be captured; then look clearly "
                    "in the desired direction until you hear the confirmation sound."
                )
                self._skip_btn.setVisible(False)
            else:
                self._feedback_message.setText(
                    "Wait until the system requests feedback."
                )
                self._skip_btn.setVisible(True)

            self._set_controls(False)

            self._pause_feedback_btn.setVisible(
                False
            )

        if (
            self._controller
            .config.mode
            == AppMode.STUDY
        ):

            self.showFullScreen()

        else:

            self.resize(
                850,
                900,
            )

            self.show()

        self.raise_()

        self.activateWindow()

        self.setFocus()

    def _on_state_updated(
        self,
        payload: dict,
    ) -> None:

        self._live_state_payload = dict(
            payload
        )

        if not self._anytime_feedback_active:

            self._maze.set_state(
                payload
            )

    def _on_anytime_feedback_started(
        self,
        payload: dict,
    ) -> None:

        self._anytime_feedback_active = True
        self._selected_history_step = None
        self._joystick_history_cursor = 0
        self._joystick_axis_latched = False
        self._joystick_button_latched = True

        self._anytime_history = list(
            payload.get(
                "history",
                [],
            )
        )

        self._pause_feedback_btn.setEnabled(
            False
        )

        self._set_controls(False)
        self._skip_btn.setVisible(False)

        self._rebuild_history_buttons()

        self._history_box.setVisible(
            True
        )

        self._maze.set_history(
            self._anytime_history
        )

        if self._feedback_modality == Modality.VOICE:
            max_box = max(1, len(self._anytime_history))
            self._feedback_message.setText(
                "Agent paused. Say the NUMBER of the state box you want "
                f"to correct (1 to {max_box})."
            )
            self._voice_recognizer.set_context(VOICE_CONTEXT_STATE_NUMBER)
        elif self._feedback_modality == Modality.JOYSTICK:
            self._highlight_joystick_history_cursor()
            self._feedback_message.setText(
                "Agent paused. Use joystick LEFT/RIGHT to choose a recent state, "
                "then press the first joystick button to confirm it."
            )
        elif self._is_gaze_modality():
            self._set_gaze_preview_active(True)
            max_box = max(1, len(self._anytime_history))
            self._feedback_message.setText(
                "Agent paused. CLOSE BOTH EYES for about 1 second, then open them. "
                f"After that, blink N times to choose box N (1 to {max_box})."
            )
            self._gaze_recognizer.set_context(GAZE_CONTEXT_LONG_CLOSE)
        else:
            self._feedback_message.setText(
                "Agent paused. Select one of the "
                "recent states below. After selecting "
                "a state, choose the corrective action."
            )

        self.setFocus()

    def _on_feedback_requested(
        self,
        payload: dict,
    ) -> None:

        self._waiting_for_feedback = (
            True
        )

        self._remaining_seconds = int(
            payload.get(
                "timeout_seconds",
                10,
            )
        )

        state = payload.get(
            "state"
        )

        if self._feedback_modality == Modality.VOICE:
            self._feedback_message.setText(
                f"Feedback requested at cell {state}. Say UP, DOWN, LEFT, "
                f"or RIGHT. Time remaining: {self._remaining_seconds} s"
            )
            self._set_controls(False)
            self._voice_recognizer.set_context(VOICE_CONTEXT_DIRECTION)
        elif self._feedback_modality == Modality.JOYSTICK:
            self._feedback_message.setText(
                f"Feedback requested at cell {state}. Tilt the joystick UP, DOWN, "
                f"LEFT, or RIGHT. Time remaining: {self._remaining_seconds} s"
            )
            self._set_controls(False)
        elif self._is_gaze_modality():
            self._set_gaze_preview_active(True)
            self._feedback_message.setText(
                f"Feedback requested at cell {state}. Look clearly in the desired "
                f"direction until you hear the confirmation sound. Missing gaze "
                f"samples are ignored. Time remaining: {self._remaining_seconds} s"
            )
            self._set_controls(False)
            self._gaze_recognizer.set_context(GAZE_CONTEXT_DIRECTION)
        else:
            self._feedback_message.setText(
                (
                    f"Feedback requested "
                    f"at cell {state}. "
                    f"Time remaining: "
                    f"{self._remaining_seconds} s"
                )
            )
            self._set_controls(True)

        self._countdown.start(
            1000
        )

        self.setFocus()

    def _on_feedback_resolved(
        self,
        payload: dict,
    ) -> None:

        if (
            not payload.get("requested")
            and self._feedback_timing
            == FeedbackTiming.ANYTIME
            and self._anytime_feedback_active
        ):

            self._anytime_feedback_active = False
            self._selected_history_step = None
            self._anytime_history = []

            self._clear_history_buttons()
            self._history_box.setVisible(False)
            self._maze.clear_history()
            self._set_gaze_preview_active(False)

            if self._live_state_payload is not None:
                self._maze.set_state(
                    self._live_state_payload
                )

            self._set_controls(False)

            if self._feedback_modality == Modality.VOICE:
                self._pause_feedback_btn.setEnabled(False)
                self._voice_recognizer.set_context(VOICE_CONTEXT_STOP)
                self._feedback_message.setText(
                    f"Feedback {payload.get('action_name')} "
                    f"applied to step {payload.get('step')} "
                    f"({payload.get('steps_back', 0)} step(s) back). "
                    'Training resumed. Say "STOP" when you want to provide '
                    "another correction."
                )
            elif self._feedback_modality == Modality.JOYSTICK:
                self._pause_feedback_btn.setEnabled(False)
                self._joystick_axis_latched = False
                self._joystick_button_latched = False
                self._feedback_message.setText(
                    f"Feedback {payload.get('action_name')} applied to step "
                    f"{payload.get('step')} ({payload.get('steps_back', 0)} step(s) back). "
                    "Training resumed. Press the first joystick button when you "
                    "want to provide another correction."
                )
            elif self._is_gaze_modality():
                self._pause_feedback_btn.setEnabled(False)
                self._gaze_recognizer.set_context(GAZE_CONTEXT_DOUBLE_BLINK)
                self._feedback_message.setText(
                    f"Feedback {payload.get('action_name')} "
                    f"applied to step {payload.get('step')} "
                    f"({payload.get('steps_back', 0)} step(s) back). "
                    "Training resumed. BLINK TWICE when you want to provide "
                    "another correction."
                )
            else:
                self._pause_feedback_btn.setEnabled(True)
                self._feedback_message.setText(
                    f"Feedback {payload.get('action_name')} "
                    f"applied to step {payload.get('step')} "
                    f"({payload.get('steps_back', 0)} step(s) back). "
                    "Training resumed. Press SPACE when you "
                    "want to provide another correction."
                )

            self.setFocus()
            return

        if payload.get(
            "requested"
        ):

            self._set_gaze_preview_active(False)
            self._waiting_for_feedback = (
                False
            )

            self._countdown.stop()
            if self._feedback_modality == Modality.VOICE:
                self._voice_recognizer.set_context(VOICE_CONTEXT_IDLE)
            elif self._feedback_modality == Modality.JOYSTICK:
                self._joystick_axis_latched = False
            elif self._is_gaze_modality():
                self._gaze_recognizer.set_context(GAZE_CONTEXT_IDLE)

            if payload.get(
                "skipped"
            ):

                text = (
                    "Feedback skipped. "
                    "Training resumed."
                )

            else:

                text = (
                    f"Feedback "
                    f"{payload.get('action_name')} "
                    f"applied. "
                    f"Training resumed."
                )

            self._feedback_message.setText(
                text
            )

            if (
                self._feedback_timing
                == FeedbackTiming.REQUESTED
            ):

                self._set_controls(
                    False
                )

    # --------------------------------------------------
    # Live HoloLens camera + gaze preview
    # --------------------------------------------------

    def _set_gaze_preview_active(self, active: bool) -> None:
        """Show/refresh the preview only during an Eye Gaze feedback interaction."""
        active = bool(active and self._is_gaze_modality())
        self._gaze_preview_box.setVisible(active)

        if active:
            self._gaze_preview_smoothed = None
            self._refresh_gaze_preview()
            self._gaze_preview_timer.start()
        else:
            self._gaze_preview_timer.stop()
            self._gaze_preview_smoothed = None
            self._gaze_preview_last_pixmap = None
            self._gaze_preview_camera.clear()
            self._gaze_preview_camera.setText(
                "Camera preview activates during Eye Gaze feedback."
            )
            self._gaze_preview_status.setText("Preview inactive")
            self._gaze_direction_debug.setText(
                "Direction debug: waiting for gaze direction recognition…"
            )

    def _refresh_gaze_preview(self) -> None:
        if not self._gaze_preview_box.isVisible():
            self._gaze_preview_timer.stop()
            return

        dm = self._controller.device_manager
        try:
            snapshot = dm.hololens_latest_camera_gaze_snapshot(distance_m=1.5)
        except Exception as exc:
            self._gaze_preview_camera.setText(
                f"HoloLens preview unavailable: {exc}"
            )
            self._gaze_preview_status.setText(
                "Check that the HoloLens is connected and receiving PV + EET data."
            )
            return

        frame = snapshot.get("frame")
        overlay = snapshot.get("gaze_overlay") or {}

        if frame is not None:
            try:
                if len(frame.shape) == 3 and frame.shape[2] == 3:
                    h, w, _ = frame.shape
                    image = QImage(
                        frame.data,
                        w,
                        h,
                        int(frame.strides[0]),
                        QImage.Format.Format_BGR888,
                    ).copy()
                elif len(frame.shape) == 2:
                    h, w = frame.shape
                    image = QImage(
                        frame.data,
                        w,
                        h,
                        int(frame.strides[0]),
                        QImage.Format.Format_Grayscale8,
                    ).copy()
                else:
                    image = QImage()

                if not image.isNull():
                    pixmap = QPixmap.fromImage(image)
                    self._draw_gaze_preview_overlay(pixmap, overlay)
                    self._gaze_preview_last_pixmap = pixmap
                    self._apply_gaze_preview_pixmap()
            except Exception as exc:
                self._gaze_preview_camera.setText(
                    f"Could not render HoloLens camera frame: {exc}"
                )
        elif self._gaze_preview_last_pixmap is None:
            self._gaze_preview_camera.setText(
                "Waiting for the first HoloLens PV camera frame…"
            )

        try:
            stats = dm.hololens_stats()
        except Exception:
            stats = {}
        eye = stats.get("latest_eye") or snapshot.get("eye") or {}
        calibration_valid = bool(eye.get("calibration_valid", False))
        camera_age = stats.get("last_camera_age_s")
        eye_age = stats.get("last_eye_age_s")

        if overlay.get("valid", False):
            visibility = (
                "gaze cursor visible"
                if overlay.get("in_frame", False)
                else "gaze is outside camera view"
            )
        else:
            visibility = "gaze cursor unavailable"

        camera_age_text = (
            "—" if camera_age is None else f"{float(camera_age):.2f}s"
        )
        eye_age_text = (
            "—" if eye_age is None else f"{float(eye_age):.2f}s"
        )
        calibration_text = "VALID" if calibration_valid else "NOT VALID"
        self._gaze_preview_status.setText(
            f"Eye calibration: {calibration_text} | {visibility} | "
            f"PV age: {camera_age_text} | EET age: {eye_age_text}"
        )
        self._gaze_preview_status.setStyleSheet(
            "font-size: 11px; font-weight: bold; color: #2e7d32;"
            if calibration_valid
            else "font-size: 11px; font-weight: bold; color: #c0392b;"
        )

    def _draw_gaze_preview_overlay(self, pixmap: QPixmap, overlay: dict) -> None:
        if not overlay.get("valid", False) or not overlay.get("in_frame", False):
            self._gaze_preview_smoothed = None
            return

        pixel = overlay.get("pixel")
        if not pixel:
            self._gaze_preview_smoothed = None
            return

        x, y = float(pixel[0]), float(pixel[1])
        if self._gaze_preview_smoothed is None:
            sx, sy = x, y
        else:
            alpha = 0.42
            sx = alpha * x + (1.0 - alpha) * self._gaze_preview_smoothed[0]
            sy = alpha * y + (1.0 - alpha) * self._gaze_preview_smoothed[1]
        self._gaze_preview_smoothed = (sx, sy)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        radius = max(10.0, min(pixmap.width(), pixmap.height()) * 0.018)
        painter.setPen(QPen(QColor("#00e5ff"), 4.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(
            int(sx - radius),
            int(sy - radius),
            int(radius * 2),
            int(radius * 2),
        )
        painter.setPen(QPen(QColor("white"), 2.0))
        cross = radius * 0.55
        painter.drawLine(int(sx - cross), int(sy), int(sx + cross), int(sy))
        painter.drawLine(int(sx), int(sy - cross), int(sx), int(sy + cross))
        painter.end()

    def _apply_gaze_preview_pixmap(self) -> None:
        if self._gaze_preview_last_pixmap is None:
            return
        self._gaze_preview_camera.setPixmap(
            self._gaze_preview_last_pixmap.scaled(
                self._gaze_preview_camera.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    # --------------------------------------------------
    # Eye-gaze feedback
    # --------------------------------------------------

    def _is_gaze_modality(self) -> bool:
        # IMPLICIT is accepted for backward compatibility with v1.1.5 data,
        # while new Study 2 runs use the explicit Eye Gaze label.
        return self._feedback_modality in (Modality.EYE_GAZE, Modality.IMPLICIT)

    def _publish_gaze_event(self, event_type: EventType, payload: dict) -> None:
        trial = self._controller.active_trial
        if trial is None:
            return
        value = ";".join(
            f"{key}={value}" for key, value in payload.items()
            if value is not None
        )
        self._controller.event_bus.publish(
            StudyEvent(
                event_type=event_type,
                participant_id=trial.participant_code,
                session_id=trial.session_id,
                trial_id=trial.trial_id,
                value=value,
            )
        )

    def _on_gaze_direction_debug(self, payload: dict) -> None:
        """Persist and display detailed gaze-direction troubleshooting data."""
        if not self._is_gaze_modality():
            return
        self._publish_gaze_event(EventType.GAZE_DIRECTION_DEBUG, payload)

        status = str(payload.get("status", ""))
        reason = str(payload.get("reason", ""))
        if status == "stale":
            age = payload.get("sample_age_seconds")
            age_text = "?" if age is None else f"{float(age):.2f}s"
            self._gaze_direction_debug.setText(
                f"Direction debug: STALE EET ({reason}); last fresh sample age={age_text}"
            )
            return
        if status == "invalid":
            self._gaze_direction_debug.setText(
                "Direction debug: INVALID — " + reason +
                f" | combined={int(bool(payload.get('combined_valid', False)))}" +
                f" left={int(bool(payload.get('left_valid', False)))}" +
                f" right={int(bool(payload.get('right_valid', False)))}"
            )
            return

        instant = str(payload.get("instant_direction", "—")).upper()
        rolling = str(payload.get("rolling_direction", "—")).upper()
        dh = payload.get("delta_horizontal_deg")
        dv = payload.get("delta_vertical_deg")
        samples = int(payload.get("valid_samples", 0) or 0)
        required = int(payload.get("required_samples", 0) or 0)
        confidence = payload.get("rolling_confidence")
        margin = payload.get("rolling_margin")
        dh_text = "—" if dh is None else f"{float(dh):+.1f}°"
        dv_text = "—" if dv is None else f"{float(dv):+.1f}°"
        conf_text = "—" if confidence is None else f"{100.0*float(confidence):.0f}%"
        margin_text = "—" if margin is None else f"{100.0*float(margin):.0f}%"
        probs = []
        for key, label in (("prob_left", "L"), ("prob_right", "R"),
                           ("prob_up", "U"), ("prob_down", "D"),
                           ("prob_center", "C")):
            value = payload.get(key)
            if value is not None:
                probs.append(f"{label}:{100.0*float(value):.0f}%")
        prob_text = " ".join(probs) if probs else "no rolling probabilities yet"
        self._gaze_direction_debug.setText(
            f"Direction debug: Δyaw={dh_text} Δpitch={dv_text} | "
            f"instant={instant} | rolling={rolling} confidence={conf_text} "
            f"margin={margin_text} | samples={samples}/{required} | {prob_text}"
        )

    def _on_gaze_gesture(self, payload: dict) -> None:
        if not self._is_gaze_modality():
            return
        self._publish_gaze_event(EventType.GAZE_GESTURE, payload)

        context = str(payload.get("context", ""))
        gesture = str(payload.get("gesture", ""))
        count = payload.get("count")
        if context == GAZE_CONTEXT_DOUBLE_BLINK and gesture == "blink":
            self._feedback_message.setText(
                f"Pause gesture: blink {count}/2 detected."
            )
        elif context == GAZE_CONTEXT_BLINK_COUNT and gesture == "blink":
            self._feedback_message.setText(
                f"State selection: {count} blink(s) detected. Keep blinking until "
                "you reach the desired box number, then keep your eyes open."
            )
        elif context == GAZE_CONTEXT_DIRECTION and gesture == "direction_window_update":
            direction = str(payload.get("direction", "")).upper()
            confidence = 100.0 * float(payload.get("confidence", 0.0))
            samples = int(payload.get("valid_samples", 0))
            required = int(payload.get("required_samples", 1))
            self._feedback_message.setText(
                f"Gaze evidence: {direction} {confidence:.0f}% "
                f"({samples}/{required} valid samples minimum). "
                "Keep looking clearly in the intended direction until you hear the beep."
            )

    def _play_gaze_direction_confirmation(self) -> None:
        """Play a short acknowledgement when a gaze direction is accepted."""
        try:
            import winsound

            # Short high tone: easy to distinguish from spoken study prompts and
            # does not require shipping an external audio asset.
            winsound.Beep(1200, 120)
            return
        except Exception:
            pass
        try:
            QApplication.beep()
        except Exception:
            pass

    def _on_gaze_command(self, payload: dict) -> None:
        if not self._is_gaze_modality():
            return
        self._publish_gaze_event(EventType.GAZE_COMMAND, payload)

        context = str(payload.get("context", ""))
        command = str(payload.get("command", ""))

        if context == GAZE_CONTEXT_DOUBLE_BLINK and command == "pause":
            if not self._begin_anytime_feedback():
                self._gaze_recognizer.set_context(GAZE_CONTEXT_DOUBLE_BLINK)
            return

        if context == GAZE_CONTEXT_LONG_CLOSE and command == "begin_blink_count":
            max_box = max(1, len(self._anytime_history))
            self._feedback_message.setText(
                "Eye-close confirmed. Now BLINK N TIMES for box N "
                f"(1 to {max_box}); then keep your eyes open for about 1 second."
            )
            self._gaze_recognizer.set_context(GAZE_CONTEXT_BLINK_COUNT)
            return

        if context == GAZE_CONTEXT_BLINK_COUNT:
            try:
                box_number = int(command)
            except ValueError:
                self._gaze_recognizer.set_context(GAZE_CONTEXT_BLINK_COUNT)
                return
            selected = next(
                (
                    item for item in self._anytime_history
                    if int(item.get("history_index", -1)) == box_number
                ),
                None,
            )
            if selected is None:
                max_box = max(1, len(self._anytime_history))
                self._feedback_message.setText(
                    f"{box_number} blinks does not match an available box. "
                    f"Blink a new count from 1 to {max_box}."
                )
                self._gaze_recognizer.set_context(GAZE_CONTEXT_BLINK_COUNT)
                return
            self._select_history_state(selected)
            return

        if context == GAZE_CONTEXT_DIRECTION:
            action_map = {
                "up": 0,
                "down": 1,
                "left": 2,
                "right": 3,
            }
            action = action_map.get(command)
            if action is None:
                self._gaze_recognizer.set_context(GAZE_CONTEXT_DIRECTION)
                return
            self._play_gaze_direction_confirmation()
            self._feedback_message.setText(
                f"{command.upper()} detected. Applying feedback."
            )
            self._send_action(action, modality=Modality.EYE_GAZE)

    def _on_gaze_error(self, message: str) -> None:
        if self._is_gaze_modality():
            self._feedback_message.setText(message)

    # --------------------------------------------------
    # Voice feedback
    # --------------------------------------------------

    def _on_voice_transcript(self, payload: dict) -> None:
        if self._feedback_modality != Modality.VOICE:
            return
        trial = self._controller.active_trial
        if trial is None:
            return
        value = (
            f"context={payload.get('context', '')};"
            f"transcript={payload.get('transcript', '')};"
            f"parsed={payload.get('command') or ''}"
        )
        self._controller.event_bus.publish(
            StudyEvent(
                event_type=EventType.VOICE_TRANSCRIPT,
                participant_id=trial.participant_code,
                session_id=trial.session_id,
                trial_id=trial.trial_id,
                value=value,
            )
        )

    def _on_voice_command(self, payload: dict) -> None:
        if self._feedback_modality != Modality.VOICE:
            return

        context = str(payload.get("context", ""))
        command = str(payload.get("command", ""))
        transcript = str(payload.get("transcript", ""))
        trial = self._controller.active_trial
        if trial is not None:
            self._controller.event_bus.publish(
                StudyEvent(
                    event_type=EventType.VOICE_COMMAND,
                    participant_id=trial.participant_code,
                    session_id=trial.session_id,
                    trial_id=trial.trial_id,
                    value=(
                        f"context={context};command={command};"
                        f"transcript={transcript}"
                    ),
                )
            )

        if context == VOICE_CONTEXT_STOP and command == "stop":
            if not self._begin_anytime_feedback():
                self._voice_recognizer.set_context(VOICE_CONTEXT_STOP)
            return

        if context == VOICE_CONTEXT_STATE_NUMBER:
            try:
                box_number = int(command)
            except ValueError:
                self._voice_recognizer.set_context(VOICE_CONTEXT_STATE_NUMBER)
                return
            selected = next(
                (
                    item for item in self._anytime_history
                    if int(item.get("history_index", -1)) == box_number
                ),
                None,
            )
            if selected is None:
                self._feedback_message.setText(
                    f"Box {box_number} is not available. Say a number shown below."
                )
                self._voice_recognizer.set_context(VOICE_CONTEXT_STATE_NUMBER)
                return
            self._select_history_state(selected)
            return

        if context == VOICE_CONTEXT_DIRECTION:
            action_map = {
                "up": 0,
                "down": 1,
                "left": 2,
                "right": 3,
            }
            action = action_map.get(command)
            if action is None:
                self._voice_recognizer.set_context(VOICE_CONTEXT_DIRECTION)
                return
            self._send_action(action, modality=Modality.VOICE)

    def _on_voice_error(self, message: str) -> None:
        if self._feedback_modality != Modality.VOICE:
            return
        # Keep the participant informed, but do not stop the RL trial.  The
        # requested-feedback timeout remains the final fallback.
        if self._waiting_for_feedback or self._anytime_feedback_active:
            self._feedback_message.setText(message)

    def _on_status_changed(
        self,
        status: str,
    ) -> None:

        self._status_label.setText(
            f"System status: {status}"
        )

        if status == "Stopped":

            self._countdown.stop()
            self._voice_recognizer.set_context(VOICE_CONTEXT_IDLE)
            self._gaze_recognizer.set_context(GAZE_CONTEXT_IDLE)
            self._set_gaze_preview_active(False)
            self._anytime_feedback_active = False
            self._selected_history_step = None
            self._anytime_history = []
            self._clear_history_buttons()
            self._history_box.setVisible(False)
            self._maze.clear_history()

    # --------------------------------------------------

    def _send_action(
        self,
        action: int,
        modality: Modality | None = None,
    ) -> None:

        submission_modality = modality or Modality.KEYBOARD

        if (
            self._feedback_timing
            == FeedbackTiming.REQUESTED
        ):

            if not self._waiting_for_feedback:
                return

            self._controller.rl_manager.submit_feedback(
                action,
                submission_modality,
            )

            return

        # Anytime feedback is only accepted after SPACE/Pause
        # and after one historical state has been selected.
        if not self._anytime_feedback_active:
            return

        if self._selected_history_step is None:
            return

        self._controller.rl_manager.submit_feedback(
            action,
            submission_modality,
            selected_step=(
                self._selected_history_step
            ),
        )

    def _begin_anytime_feedback(
        self,
    ) -> bool:

        if (
            self._feedback_timing
            != FeedbackTiming.ANYTIME
        ):
            return False

        if self._anytime_feedback_active:
            return False

        return self._controller.rl_manager.begin_anytime_feedback()

    def _clear_history_buttons(
        self,
    ) -> None:

        while self._history_layout.count():

            item = self._history_layout.takeAt(0)
            widget = item.widget()

            if widget is not None:
                widget.deleteLater()

        self._history_buttons = []

    def _rebuild_history_buttons(
        self,
    ) -> None:

        self._clear_history_buttons()

        for index, item in enumerate(
            self._anytime_history
        ):

            state = tuple(
                item.get(
                    "state",
                    ("?", "?"),
                )
            )

            step = item.get(
                "step",
                "?",
            )

            steps_back = item.get(
                "steps_back",
                "?",
            )

            history_index = item.get(
                "history_index",
                index + 1,
            )

            button = QPushButton(
                f"{history_index}. Step {step}\n"
                f"Cell {state}  |  "
                f"{steps_back} back"
            )

            button.setMinimumHeight(
                58
            )

            button.setFocusPolicy(
                Qt.FocusPolicy.NoFocus
            )
            if self._feedback_modality in (Modality.VOICE, Modality.JOYSTICK) or self._is_gaze_modality():
                # Voice/gaze trials show numbered boxes as visual references,
                # but selection itself must use the assigned modality.
                button.setEnabled(False)

            button.clicked.connect(
                lambda checked=False, record=item:
                    self._select_history_state(
                        record
                    )
            )

            row = index // 5
            col = index % 5

            self._history_layout.addWidget(
                button,
                row,
                col,
            )

            self._history_buttons.append(
                (button, item)
            )

    def _select_history_state(
        self,
        item: dict,
    ) -> None:

        if not self._anytime_feedback_active:
            return

        self._selected_history_step = int(
            item["step"]
        )

        for button, record in self._history_buttons:

            is_selected = (
                record.get("step")
                == self._selected_history_step
            )

            button.setStyleSheet(
                "font-weight: bold; "
                "border: 3px solid #2a9d8f;"
                if is_selected
                else ""
            )

        if self._live_state_payload is not None:

            preview = dict(
                self._live_state_payload
            )

            preview["agent_position"] = tuple(
                item["state"]
            )

            preview["ambiguous_position"] = None

            self._maze.set_state(
                preview
            )

        self._maze.set_history(
            self._anytime_history,
            selected_step=(
                self._selected_history_step
            ),
        )

        self._skip_btn.setVisible(False)

        if self._feedback_modality == Modality.VOICE:
            self._set_controls(False)
            self._feedback_message.setText(
                f"Selected box {item.get('history_index', '?')} — step "
                f"{item['step']} at cell {tuple(item['state'])}. "
                "Now say UP, DOWN, LEFT, or RIGHT."
            )
            self._voice_recognizer.set_context(VOICE_CONTEXT_DIRECTION)
        elif self._feedback_modality == Modality.JOYSTICK:
            self._set_controls(False)
            self._joystick_axis_latched = True
            self._feedback_message.setText(
                f"Selected box {item.get('history_index', '?')} — step "
                f"{item['step']} at cell {tuple(item['state'])}. "
                "Return the joystick to center, then tilt it UP, DOWN, LEFT, "
                "or RIGHT for the corrective action."
            )
        elif self._is_gaze_modality():
            self._set_controls(False)
            self._feedback_message.setText(
                f"Selected box {item.get('history_index', '?')} — step "
                f"{item['step']} at cell {tuple(item['state'])}. "
                "Now look clearly in the corrective direction until "
                "you hear the confirmation sound. Missing gaze samples are ignored."
            )
            self._gaze_recognizer.set_context(GAZE_CONTEXT_DIRECTION)
        else:
            self._set_controls(True)
            self._feedback_message.setText(
                f"Selected step {item['step']} at "
                f"cell {tuple(item['state'])}. "
                "Now choose UP, DOWN, LEFT, or RIGHT."
            )

        self.setFocus()

    def _skip(self) -> None:

        if (
            self._feedback_timing
            == FeedbackTiming.REQUESTED

            and self._waiting_for_feedback
        ):

            self._controller.rl_manager.submit_feedback(
                None,
                Modality.KEYBOARD,
            )

    def _set_controls(
        self,
        enabled: bool,
    ) -> None:

        for button in (
            self._up_btn,
            self._down_btn,
            self._left_btn,
            self._right_btn,
            self._skip_btn,
        ):

            button.setEnabled(
                enabled
            )

    def _joystick_direction(self, axes: list[float]) -> int | None:
        """Map the first two joystick axes to Gridworld actions.

        Action order follows the Gridworld convention used by the keyboard:
        0=Up, 1=Down, 2=Left, 3=Right.  The dominant axis wins so diagonal
        deflections still produce one unambiguous correction.
        """
        if len(axes) < 2:
            return None
        x = float(axes[0])
        y = float(axes[1])
        threshold = 0.55
        if max(abs(x), abs(y)) < threshold:
            return None
        if abs(y) >= abs(x):
            return 0 if y < 0 else 1
        return 2 if x < 0 else 3

    def _highlight_joystick_history_cursor(self) -> None:
        if not self._history_buttons:
            return
        self._joystick_history_cursor = max(
            0, min(self._joystick_history_cursor, len(self._history_buttons) - 1)
        )
        for index, (button, _record) in enumerate(self._history_buttons):
            if self._selected_history_step is not None:
                continue
            button.setStyleSheet(
                "font-weight: bold; border: 3px dashed #457b9d;"
                if index == self._joystick_history_cursor
                else ""
            )
        current = self._history_buttons[self._joystick_history_cursor][1]
        self._maze.set_history(
            self._anytime_history,
            selected_step=current.get("step"),
        )

    def _poll_joystick_feedback(self) -> None:
        """Read joystick input without relabeling keyboard/mouse actions.

        Requested mode: tilt the stick to submit a direction.
        Anytime mode: button 1 pauses; LEFT/RIGHT chooses a history box;
        button 1 confirms it; the next directional stick tilt is submitted.
        """
        if self._feedback_modality != Modality.JOYSTICK:
            self._joystick_axis_latched = False
            self._joystick_button_latched = False
            return

        try:
            stats = self._controller.device_manager.joystick_stats()
        except Exception:
            return
        axes = [float(v) for v in stats.get("axes", [])]
        buttons = stats.get("buttons", [])
        pressed = bool(buttons and buttons[0])
        button_rising = pressed and not self._joystick_button_latched
        self._joystick_button_latched = pressed

        x = axes[0] if len(axes) > 0 else 0.0
        y = axes[1] if len(axes) > 1 else 0.0
        if abs(x) < 0.30 and abs(y) < 0.30:
            self._joystick_axis_latched = False

        if self._feedback_timing == FeedbackTiming.REQUESTED:
            if not self._waiting_for_feedback or self._joystick_axis_latched:
                return
            action = self._joystick_direction(axes)
            if action is not None:
                self._joystick_axis_latched = True
                self._send_action(action, modality=Modality.JOYSTICK)
            return

        if self._feedback_timing != FeedbackTiming.ANYTIME:
            return

        if not self._anytime_feedback_active:
            if button_rising:
                self._begin_anytime_feedback()
            return

        if self._selected_history_step is None:
            if not self._history_buttons:
                return
            if not self._joystick_axis_latched and abs(x) >= 0.55:
                direction = -1 if x < 0 else 1
                self._joystick_history_cursor = (
                    self._joystick_history_cursor + direction
                ) % len(self._history_buttons)
                self._joystick_axis_latched = True
                self._highlight_joystick_history_cursor()
            if button_rising:
                _button, record = self._history_buttons[self._joystick_history_cursor]
                self._select_history_state(record)
            return

        if self._joystick_axis_latched:
            return
        action = self._joystick_direction(axes)
        if action is not None:
            self._joystick_axis_latched = True
            self._send_action(action, modality=Modality.JOYSTICK)

    def _tick_countdown(
        self,
    ) -> None:

        if not self._waiting_for_feedback:

            self._countdown.stop()

            return

        self._remaining_seconds = max(
            0,
            self._remaining_seconds - 1,
        )

        if self._feedback_modality == Modality.VOICE:
            self._feedback_message.setText(
                "Feedback requested. Say UP, DOWN, LEFT, or RIGHT. "
                f"Time remaining: {self._remaining_seconds} s"
            )
        elif self._feedback_modality == Modality.JOYSTICK:
            self._feedback_message.setText(
                "Feedback requested. Tilt the joystick UP, DOWN, LEFT, or RIGHT. "
                f"Time remaining: {self._remaining_seconds} s"
            )
        elif self._is_gaze_modality():
            self._feedback_message.setText(
                "Feedback requested. Look normally at the agent/maze for the center sample, "
                "then look clearly in the desired direction until you hear the beep. "
                f"Time remaining: {self._remaining_seconds} s"
            )
        else:
            self._feedback_message.setText(
                (
                    "Feedback requested. "
                    f"Time remaining: "
                    f"{self._remaining_seconds} s"
                )
            )

        if (
            self._remaining_seconds
            <= 0
        ):

            self._countdown.stop()

    # --------------------------------------------------
    # Keyboard feedback
    # --------------------------------------------------

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_gaze_preview_pixmap()
        if hasattr(self, "_start_overlay"):
            self._start_overlay.setGeometry(self.rect())
            if self._start_overlay.isVisible():
                self._start_overlay.raise_()

    def keyPressEvent(
        self,
        event: QKeyEvent,
    ) -> None:

        if self._feedback_modality in (Modality.VOICE, Modality.JOYSTICK) or self._is_gaze_modality():
            # Do not silently turn keyboard presses into Voice/Eye-Gaze observations.
            # These conditions are controlled only by their assigned modality.
            if event.key() in (
                Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Left, Qt.Key.Key_Right,
                Qt.Key.Key_W, Qt.Key.Key_A, Qt.Key.Key_S, Qt.Key.Key_D,
                Qt.Key.Key_Space,
            ):
                event.accept()
                return

        key_map = {
            Qt.Key.Key_Up: 0,
            Qt.Key.Key_W: 0,

            Qt.Key.Key_Down: 1,
            Qt.Key.Key_S: 1,

            Qt.Key.Key_Left: 2,
            Qt.Key.Key_A: 2,

            Qt.Key.Key_Right: 3,
            Qt.Key.Key_D: 3,
        }

        if event.key() in key_map:

            self._send_action(
                key_map[
                    event.key()
                ]
            )

            event.accept()

            return

        if (
            event.key()
            == Qt.Key.Key_Space
        ):

            if (
                self._feedback_timing
                == FeedbackTiming.ANYTIME
            ):

                self._begin_anytime_feedback()

            else:

                self._skip()

            event.accept()

            return

        super().keyPressEvent(
            event
        )
