"""Fullscreen participant video player for Study 3 agent observation."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QCloseEvent, QResizeEvent
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from core.application_controller import ApplicationController
from gui.participant_start_overlay import ParticipantStartOverlay
from models.enums import EventType, Study
from models.event import StudyEvent


class ObservationVideoWindow(QWidget):
    """Owns the Study 3 participant Start gate and fullscreen playback."""

    def __init__(self, controller: ApplicationController) -> None:
        super().__init__()
        self._controller = controller
        self._trial_id = ""
        self._source_path: Path | None = None
        self._finishing = False

        self.setWindowTitle("HINT Study 3 — Agent Observation")
        self.setStyleSheet("background: black; color: white;")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._video_widget = QVideoWidget()
        self._video_widget.setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatio)
        root.addWidget(self._video_widget, 1)

        self._status = QLabel("")
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status.setWordWrap(True)
        self._status.setStyleSheet(
            "background: black; color: white; font-size: 22px; padding: 18px;"
        )
        self._status.hide()
        root.addWidget(self._status)

        self._audio = QAudioOutput(self)
        self._audio.setVolume(1.0)
        self._player = QMediaPlayer(self)
        self._player.setAudioOutput(self._audio)
        self._player.setVideoOutput(self._video_widget)
        self._player.mediaStatusChanged.connect(self._on_media_status_changed)
        self._player.errorOccurred.connect(self._on_media_error)

        self._start_overlay = ParticipantStartOverlay(self)
        self._start_overlay.start_requested.connect(self._participant_start_requested)
        controller.event_bus.event_published.connect(self._on_lifecycle_event)

    def _on_lifecycle_event(self, event: StudyEvent) -> None:
        if event.event_type == EventType.ACTIVITY_PREPARED:
            trial = self._controller.active_trial
            if (
                trial is None
                or trial.trial_id != event.trial_id
                or trial.condition.study != Study.OBSERVATION
            ):
                return
            source = self._controller.observation_video_path_for_trial(trial.trial_id)
            if source is None:
                return
            self._prepare(trial, source)
            return

        if not event.trial_id or event.trial_id != self._trial_id:
            return
        if event.event_type == EventType.PARTICIPANT_ACTIVITY_STARTED:
            self._start_overlay.start_succeeded(event.trial_id)
        elif event.event_type == EventType.TRIAL_ENDED:
            self._stop_and_hide(event.trial_id)

    def _prepare(self, trial, source: Path) -> None:
        self._player.stop()
        self._trial_id = trial.trial_id
        self._source_path = Path(source)
        self._finishing = False
        self._status.hide()

        # QVideoWidget uses a native video surface on Windows.  A visible native
        # child can cover ordinary sibling widgets even after raise_(), which
        # made the black, stopped video surface hide the participant Start gate.
        # Keep the surface out of the native window stack until Start is accepted.
        self._video_widget.hide()
        self._player.setSource(QUrl.fromLocalFile(str(self._source_path)))
        self.showFullScreen()
        self.raise_()
        self.activateWindow()
        self.unsetCursor()
        self._start_overlay.present(trial)

    def _participant_start_requested(self, trial_id: str) -> None:
        if trial_id != self._trial_id:
            return
        try:
            self._controller.start_prepared_activity(trial_id)

            # PARTICIPANT_ACTIVITY_STARTED synchronously dismisses the Start
            # gate.  Only now expose the native video surface and begin playback.
            self._video_widget.show()
            self._player.play()
            self.setCursor(Qt.CursorShape.BlankCursor)
            self._controller.publish_observation_video_event(
                EventType.OBSERVATION_VIDEO_STARTED,
                trial_id,
                str(self._source_path or ""),
            )
        except Exception as exc:
            self._start_overlay.start_failed(
                str(exc), can_retry=not self._controller.activity_started
            )

    def _on_media_status_changed(self, status: QMediaPlayer.MediaStatus) -> None:
        if status != QMediaPlayer.MediaStatus.EndOfMedia:
            return
        if not self._trial_id or self._finishing:
            return
        if not self._controller.activity_started:
            return
        self._finishing = True
        trial_id = self._trial_id
        self._controller.publish_observation_video_event(
            EventType.OBSERVATION_VIDEO_ENDED,
            trial_id,
            str(self._source_path or ""),
        )
        try:
            completed = self._controller.complete_active_observation_video(trial_id)
            if not completed:
                raise RuntimeError("The active observation run no longer matches this video")
        except Exception as exc:
            self._finishing = False
            self._show_error(
                "The video finished, but the run could not be finalized.\n"
                f"Please tell the researcher.\n\n{exc}"
            )

    def _on_media_error(self, _error, message: str) -> None:
        if not self._trial_id:
            return
        detail = message or self._player.errorString() or "Unknown media playback error"
        self._controller.publish_observation_video_event(
            EventType.OBSERVATION_VIDEO_ERROR, self._trial_id, detail
        )
        if not self._controller.activity_started:
            self._start_overlay.start_failed(detail, can_retry=False)
            return
        self._show_error(
            "The observation video could not be played.\n"
            "Please tell the researcher.\n\n"
            f"{detail}"
        )

    def _show_error(self, message: str) -> None:
        self.unsetCursor()
        self._video_widget.hide()
        self._status.setText(message)
        self._status.show()

    def _stop_and_hide(self, trial_id: str) -> None:
        if trial_id != self._trial_id:
            return
        self._player.stop()
        self._start_overlay.dismiss(trial_id)
        self._video_widget.show()
        self._status.hide()
        self.unsetCursor()
        self.hide()
        self._trial_id = ""
        self._source_path = None
        self._finishing = False

    def closeEvent(self, event: QCloseEvent) -> None:
        # Closing the researcher console owns shutdown. An accidental Alt+F4 on
        # the participant display must not leave sensors recording invisibly.
        if self._trial_id and self._controller.active_trial is not None:
            event.ignore()
            return
        self._player.stop()
        super().closeEvent(event)

    def resizeEvent(self, event: QResizeEvent) -> None:
        """Keep the participant gate fitted while fullscreen geometry settles."""
        super().resizeEvent(event)
        if self._start_overlay.isVisible():
            self._start_overlay.setGeometry(self.rect())
