from __future__ import annotations

from PySide6.QtCore import (
    Qt,
    QTimer,
)

from PySide6.QtGui import (
    QColor,
    QKeyEvent,
    QPainter,
    QPen,
)

from PySide6.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.application_controller import (
    ApplicationController,
)

from models.enums import (
    AppMode,
    FeedbackTiming,
    Modality,
)


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

        feedback_layout.addLayout(
            grid
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

    # --------------------------------------------------

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
        self._live_state_payload = None
        self._clear_history_buttons()
        self._maze.clear_history()
        self._history_box.setVisible(False)

        if (
            self._feedback_timing
            == FeedbackTiming.ANYTIME
        ):

            self._feedback_message.setText(
                "When you want to give feedback, "
                "press SPACE or the Pause button. "
                "Then choose a recent state and "
                "provide the corrective action."
            )

            # Direction controls are only enabled after
            # the participant explicitly selects a state.
            self._set_controls(False)

            self._skip_btn.setVisible(
                False
            )

            self._pause_feedback_btn.setVisible(
                True
            )

            self._pause_feedback_btn.setEnabled(
                True
            )

        else:

            self._feedback_message.setText(
                "Wait until the system "
                "requests feedback."
            )

            self._set_controls(False)

            self._skip_btn.setVisible(
                True
            )

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

            if self._live_state_payload is not None:
                self._maze.set_state(
                    self._live_state_payload
                )

            self._set_controls(False)
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

            self._waiting_for_feedback = (
                False
            )

            self._countdown.stop()

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

    def _on_status_changed(
        self,
        status: str,
    ) -> None:

        self._status_label.setText(
            f"System status: {status}"
        )

        if status == "Stopped":

            self._countdown.stop()
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
    ) -> None:

        if (
            self._feedback_timing
            == FeedbackTiming.REQUESTED
        ):

            if not self._waiting_for_feedback:
                return

            self._controller.rl_manager.submit_feedback(
                action,
                Modality.KEYBOARD,
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
            Modality.KEYBOARD,
            selected_step=(
                self._selected_history_step
            ),
        )

    def _begin_anytime_feedback(
        self,
    ) -> None:

        if (
            self._feedback_timing
            != FeedbackTiming.ANYTIME
        ):
            return

        if self._anytime_feedback_active:
            return

        self._controller.rl_manager.begin_anytime_feedback()

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

        self._set_controls(True)
        self._skip_btn.setVisible(False)

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

    def keyPressEvent(
        self,
        event: QKeyEvent,
    ) -> None:

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