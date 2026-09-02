"""Researcher dialog for choosing a local or external study-data folder."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.storage_location import (
    configured_data_root,
    load_storage_location,
    save_storage_location,
    validate_storage_location,
)


def _format_gib(byte_count: int) -> str:
    return f"{byte_count / (1024 ** 3):.1f} GB"


class StorageLocationDialog(QDialog):
    def __init__(self, config_dir: Path, project_root: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config_dir = Path(config_dir)
        self._project_root = Path(project_root)
        settings = load_storage_location(self._config_dir)

        self.setWindowTitle("HINT Data Storage Location")
        self.setMinimumWidth(680)
        layout = QVBoxLayout(self)

        title = QLabel("Choose where all HINT study data will be saved")
        title.setStyleSheet("font-size: 17px; font-weight: bold;")
        layout.addWidget(title)

        explanation = QLabel(
            "Participant folders, HoloLens/Shimmer recordings, RL files, both SQLite "
            "databases, and application logs will use this location. For real data "
            "collection, choose a dedicated folder on the external hard drive."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        row = QHBoxLayout()
        self._path = QLineEdit(str(configured_data_root(self._config_dir, self._project_root)))
        self._path.setMinimumWidth(500)
        row.addWidget(self._path, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        layout.addLayout(row)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        self._prompt = QCheckBox("Ask me to confirm the data location whenever the app starts")
        self._prompt.setChecked(settings.prompt_on_startup)
        layout.addWidget(self._prompt)

        warning = QLabel(
            "Important: keep the selected drive connected for the entire study run. "
            "The app will stop at startup if a remembered external location is unavailable. "
            "Each location has its own participant databases and ID sequence; existing "
            "data is not automatically copied when you switch folders."
        )
        warning.setWordWrap(True)
        warning.setStyleSheet("color: #8a4b00;")
        layout.addWidget(warning)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Save && Continue")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._refresh_status()

    @property
    def selected_root(self) -> Path:
        return Path(self._path.text().strip()).expanduser()

    def _browse(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self,
            "Select HINT study-data folder",
            str(self.selected_root),
            QFileDialog.Option.ShowDirsOnly,
        )
        if chosen:
            self._path.setText(chosen)
            self._refresh_status()

    def _refresh_status(self) -> None:
        ok, message, free_bytes = validate_storage_location(self.selected_root, create=False)
        if ok and free_bytes is not None:
            message += f" Free space: {_format_gib(free_bytes)}."
        self._status.setText(message)
        self._status.setStyleSheet("color: #1b5e20;" if ok else "color: #b71c1c;")

    def _save(self) -> None:
        raw = self._path.text().strip()
        if not raw:
            QMessageBox.warning(self, "Storage folder required", "Choose a data-storage folder.")
            return
        root = Path(raw).expanduser()
        ok, message, free_bytes = validate_storage_location(root, create=True)
        if not ok:
            QMessageBox.critical(self, "Storage folder unavailable", message)
            self._refresh_status()
            return
        try:
            save_storage_location(
                self._config_dir,
                root,
                prompt_on_startup=self._prompt.isChecked(),
            )
        except OSError as exc:
            QMessageBox.critical(self, "Could not save storage setting", str(exc))
            return
        self._status.setText(
            f"Storage folder is ready. Free space: {_format_gib(free_bytes or 0)}."
        )
        self.accept()


def ensure_storage_location(
    config_dir: Path,
    project_root: Path,
    *,
    force_prompt: bool = False,
    parent: QWidget | None = None,
) -> Path | None:
    settings = load_storage_location(config_dir)
    selected = configured_data_root(config_dir, project_root)
    ok, _message, _free = validate_storage_location(selected, create=False)
    if not force_prompt and not settings.prompt_on_startup and ok:
        return selected.resolve()

    dialog = StorageLocationDialog(config_dir, project_root, parent)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return dialog.selected_root.resolve()
