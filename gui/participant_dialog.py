"""New Participant registration dialog.

Per the IRB protocol's registration form (name, email, and demographics
collected separately from identifiable data), and the app's existing
PII-separation design: name/email are identifying and stored only in the
identifiable database; age is stored as a demographic on the pseudonymous
experimental record.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QSpinBox,
    QVBoxLayout,
)

from core.application_controller import ApplicationController
from models.participant import ParticipantRecord


class NewParticipantDialog(QDialog):
    def __init__(self, controller: ApplicationController, parent=None) -> None:
        super().__init__(parent)
        self._controller = controller
        self.created_participant_code: str | None = None

        self.setWindowTitle("Register Participant")
        self.setMinimumWidth(380)

        root = QVBoxLayout(self)
        form = QFormLayout()

        self._name_input = QLineEdit()
        self._name_input.setPlaceholderText("Full name")
        form.addRow("Name:", self._name_input)

        self._age_input = QSpinBox()
        self._age_input.setRange(0, 120)
        self._age_input.setSpecialValueText("Not specified")
        self._age_input.setValue(0)
        form.addRow("Age:", self._age_input)

        self._email_input = QLineEdit()
        self._email_input.setPlaceholderText("name@example.com")
        form.addRow("Email:", self._email_input)

        self._phone_input = QLineEdit()
        self._phone_input.setPlaceholderText("(optional)")
        form.addRow("Phone:", self._phone_input)

        root.addLayout(form)

        note = QLabel(
            "A pseudonymous participant code (e.g. P004) will be generated. "
            "Name/email/phone are stored separately from study data; only "
            "the code is used in every other part of the app."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #666; font-size: 11px;")
        root.addWidget(note)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Register")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _on_accept(self) -> None:
        name = self._name_input.text().strip()
        if not name:
            QMessageBox.warning(self, "Name required", "Please enter the participant's name.")
            return

        age = self._age_input.value() or None

        try:
            record: ParticipantRecord = self._controller.participant_manager.create_participant(
                name=name,
                email=self._email_input.text().strip(),
                phone=self._phone_input.text().strip(),
                age=age,
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Could not register participant", str(exc))
            return

        self.created_participant_code = record.participant_code
        self.accept()
