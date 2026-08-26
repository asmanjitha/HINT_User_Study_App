"""Registration step detail panel.

Shows the selected participant's identity + demographics and lets the
researcher correct typos after the fact. Registration itself (creating a
brand-new participant) happens via the "New Participant" button/dialog in
the Workflow page's top bar -- this panel is what "Registration" looks
like once you've selected that step for an already-registered participant.
"""

from __future__ import annotations

import datetime

from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.application_controller import ApplicationController


class RegistrationPanel(QWidget):
    def __init__(self, controller: ApplicationController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._controller = controller
        self._participant_code: str | None = None

        root = QVBoxLayout(self)

        title = QLabel("Registration")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        root.addWidget(title)

        box = QGroupBox("Participant Details")
        form = QFormLayout(box)

        self._code_label = QLabel("--")
        form.addRow("Participant code:", self._code_label)

        self._name_input = QLineEdit()
        form.addRow("Name:", self._name_input)

        self._age_input = QSpinBox()
        self._age_input.setRange(0, 120)
        self._age_input.setSpecialValueText("Not specified")
        form.addRow("Age:", self._age_input)

        self._email_input = QLineEdit()
        form.addRow("Email:", self._email_input)

        self._phone_input = QLineEdit()
        form.addRow("Phone:", self._phone_input)

        self._created_label = QLabel("--")
        form.addRow("Registered on:", self._created_label)

        self._sessions_label = QLabel("--")
        form.addRow("Sessions on record:", self._sessions_label)

        root.addWidget(box)

        save_btn = QPushButton("Save Changes")
        save_btn.clicked.connect(self._save)
        root.addWidget(save_btn)

        note = QLabel(
            "Name/email/phone are stored only in the identifiable database and are "
            "never written into session data; everything else in the app only ever "
            "sees the participant code above."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #666; font-size: 11px;")
        root.addWidget(note)

        root.addStretch()

    def set_participant(self, participant_code: str | None) -> None:
        self._participant_code = participant_code
        self.refresh()

    def refresh(self) -> None:
        if self._participant_code is None:
            self._code_label.setText("--")
            self._name_input.clear()
            self._age_input.setValue(0)
            self._email_input.clear()
            self._phone_input.clear()
            self._created_label.setText("--")
            self._sessions_label.setText("--")
            self.setEnabled(False)
            return

        self.setEnabled(True)
        identity = self._controller.participant_manager.get_identity(self._participant_code)
        record = self._controller.participant_manager.get_record(self._participant_code)

        self._code_label.setText(self._participant_code)
        self._name_input.setText(identity.name if identity else "")
        self._age_input.setValue(int(record.demographics.get("age") or 0) if record else 0)
        self._email_input.setText(identity.email if identity else "")
        self._phone_input.setText(identity.phone if identity else "")

        if identity is not None:
            self._created_label.setText(
                datetime.datetime.fromtimestamp(identity.created_at).strftime("%Y-%m-%d %H:%M")
            )

        sessions = self._controller.session_manager.list_sessions_for_participant(self._participant_code)
        self._sessions_label.setText(str(len(sessions)))

    def _save(self) -> None:
        if self._participant_code is None:
            return

        name = self._name_input.text().strip()
        if not name:
            QMessageBox.warning(self, "Name required", "Name cannot be empty.")
            return

        self._controller.participant_manager.edit_identity(
            self._participant_code,
            name=name,
            email=self._email_input.text().strip(),
            phone=self._phone_input.text().strip(),
        )
        self._controller.participant_manager.update_demographics(
            self._participant_code, age=(self._age_input.value() or None)
        )
        QMessageBox.information(self, "Saved", "Participant details updated.")
