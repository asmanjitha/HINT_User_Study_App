"""Reusable participant-side confirmation gate for every activity window."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget


class ParticipantStartOverlay(QWidget):
    """Opaque full-window page shown after researcher preparation."""

    start_requested = Signal(str)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._trial_id = ""
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("background: #f3f7fb;")

        root = QVBoxLayout(self)
        root.setContentsMargins(70, 70, 70, 70)
        root.addStretch(2)

        title = QLabel("Ready to begin?")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 38px; font-weight: bold; color: #17324d;")
        root.addWidget(title)

        self._activity = QLabel("Your activity is ready.")
        self._activity.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._activity.setWordWrap(True)
        self._activity.setStyleSheet("font-size: 20px; color: #334e68; padding: 18px;")
        root.addWidget(self._activity)

        instructions = QLabel(
            "Press Start Activity when you are comfortable and ready. "
            "Your activity time begins only after you press the button."
        )
        instructions.setAlignment(Qt.AlignmentFlag.AlignCenter)
        instructions.setWordWrap(True)
        instructions.setStyleSheet("font-size: 18px; color: #486581; padding: 12px;")
        root.addWidget(instructions)

        self._start_button = QPushButton("START ACTIVITY")
        self._start_button.setMinimumHeight(88)
        self._start_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._start_button.setStyleSheet(
            "QPushButton { background: #167d4b; color: white; border: none; "
            "border-radius: 10px; font-size: 28px; font-weight: bold; padding: 18px; } "
            "QPushButton:hover { background: #11683e; } "
            "QPushButton:disabled { background: #8ca59a; }"
        )
        self._start_button.clicked.connect(self._request_start)
        root.addWidget(self._start_button)

        self._message = QLabel("")
        self._message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._message.setWordWrap(True)
        self._message.setStyleSheet("font-size: 16px; color: #9b2c2c; padding: 10px;")
        root.addWidget(self._message)
        root.addStretch(3)

        self.hide()

    def present(self, trial) -> None:
        self._trial_id = trial.trial_id
        label = trial.condition_name or trial.readable_run_label
        kind = "Training activity" if trial.practice else "Study activity"
        self._activity.setText(f"{kind}: {label}")
        self._message.clear()
        self._start_button.setText("START ACTIVITY")
        self._start_button.setEnabled(True)
        self.setGeometry(self.parentWidget().rect())
        self.show()
        self.raise_()
        self._start_button.setFocus()

    def start_succeeded(self, trial_id: str) -> None:
        if trial_id == self._trial_id:
            self.hide()

    def start_failed(self, message: str, *, can_retry: bool) -> None:
        first_line = str(message).splitlines()[0] if str(message).splitlines() else str(message)
        self._message.setText(
            f"The activity could not start: {first_line}\nPlease tell the researcher."
        )
        self._start_button.setText("TRY START AGAIN" if can_retry else "START FAILED")
        self._start_button.setEnabled(can_retry)
        self.raise_()

    def dismiss(self, trial_id: str) -> None:
        if trial_id == self._trial_id:
            self.hide()
            self._trial_id = ""

    def _request_start(self) -> None:
        if not self._trial_id:
            return
        self._start_button.setText("STARTING…")
        self._start_button.setEnabled(False)
        self._message.setText("Please wait while the activity starts.")
        self.start_requested.emit(self._trial_id)
