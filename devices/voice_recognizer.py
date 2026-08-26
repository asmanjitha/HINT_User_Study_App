"""Offline voice-command recognition for HINT Gridworld feedback.

The recognizer reuses the microphone stream already selected/connected on the
Devices page. ``MicrophoneDevice.capture_phrase`` provides PCM audio, so Vosk
does not open a competing microphone stream.

Vosk is deliberately used locally for the human-subject study: participant
audio is recognized on the study PC rather than sent to a cloud speech API.
The first model initialization may download Vosk's small English model if it is
not already cached; subsequent recognition is local/offline.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, Signal

from devices.input_devices import MicrophoneDevice

logger = logging.getLogger(__name__)

VOICE_CONTEXT_IDLE = "idle"
VOICE_CONTEXT_STOP = "stop"
VOICE_CONTEXT_STATE_NUMBER = "state_number"
VOICE_CONTEXT_DIRECTION = "direction"

_VALID_CONTEXTS = {
    VOICE_CONTEXT_IDLE,
    VOICE_CONTEXT_STOP,
    VOICE_CONTEXT_STATE_NUMBER,
    VOICE_CONTEXT_DIRECTION,
}

_DIRECTION_WORDS = {
    "up": "up",
    "down": "down",
    "left": "left",
    "right": "right",
}

_NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def parse_voice_command(context: str, transcript: str) -> Optional[str]:
    """Return the canonical command for one recognition context."""

    tokens = _tokens(transcript)
    if not tokens:
        return None

    if context == VOICE_CONTEXT_STOP:
        return "stop" if "stop" in tokens else None

    if context == VOICE_CONTEXT_DIRECTION:
        for token in tokens:
            command = _DIRECTION_WORDS.get(token)
            if command is not None:
                return command
        return None

    if context == VOICE_CONTEXT_STATE_NUMBER:
        for token in tokens:
            if token.isdigit():
                number = int(token)
                if 1 <= number <= 10:
                    return str(number)
            number = _NUMBER_WORDS.get(token)
            if number is not None:
                return str(number)
        return None

    return None


def _grammar_for_context(context: str) -> list[str]:
    if context == VOICE_CONTEXT_STOP:
        return ["stop", "[unk]"]
    if context == VOICE_CONTEXT_DIRECTION:
        return [
            "up", "down", "left", "right",
            "move up", "move down", "move left", "move right",
            "go up", "go down", "go left", "go right",
            "[unk]",
        ]
    if context == VOICE_CONTEXT_STATE_NUMBER:
        words = list(_NUMBER_WORDS)
        return words + [f"box {word}" for word in words] + [
            f"state {word}" for word in words
        ] + ["[unk]"]
    return ["[unk]"]


class VoiceCommandRecognizer(QObject):
    """Background local phrase recognizer with a constrained vocabulary."""

    transcript_heard = Signal(object)
    command_recognized = Signal(object)
    status_changed = Signal(str)
    recognition_error = Signal(str)

    _shared_model = None
    _shared_model_error = ""
    _shared_model_lock = threading.Lock()

    def __init__(
        self,
        microphone: MicrophoneDevice,
        config: dict | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._microphone = microphone
        self._config = dict(config or {})
        self._language = str(self._config.get("language", "en-us"))
        self._model_path = str(self._config.get("model_path", "")).strip()
        self._listen_window_s = float(self._config.get("listen_window_seconds", 0.75))
        self._phrase_time_limit_s = float(
            self._config.get("phrase_time_limit_seconds", 2.5)
        )
        self._silence_s = float(self._config.get("silence_seconds", 0.40))
        self._activation_peak = float(self._config.get("activation_peak", 0.012))
        self._context = VOICE_CONTEXT_IDLE
        self._context_lock = threading.Lock()
        self._shutdown = threading.Event()
        self._last_error_emit = 0.0

        try:
            import vosk  # type: ignore

            self._vosk = vosk
            try:
                vosk.SetLogLevel(-1)
            except Exception:
                pass
        except Exception:
            self._vosk = None

        # Start model preparation immediately in the background. In the normal
        # protocol there is ample time before Study 2 Voice conditions begin.
        self._preload_worker = threading.Thread(
            target=self._preload_model,
            name="HintVoiceModelPreload",
            daemon=True,
        )
        self._preload_worker.start()

        self._worker = threading.Thread(
            target=self._run,
            name="HintVoiceCommandRecognizer",
            daemon=True,
        )
        self._worker.start()

    @staticmethod
    def backend_available() -> bool:
        try:
            import vosk  # noqa: F401

            return True
        except Exception:
            return False

    @property
    def available(self) -> bool:
        return self._vosk is not None

    @property
    def backend_error(self) -> str:
        return type(self)._shared_model_error

    def set_context(self, context: str) -> None:
        if context not in _VALID_CONTEXTS:
            raise ValueError(f"Unknown voice-recognition context: {context}")
        with self._context_lock:
            changed = context != self._context
            self._context = context
        if changed:
            self.status_changed.emit(context)

    def context(self) -> str:
        with self._context_lock:
            return self._context

    def shutdown(self) -> None:
        self._shutdown.set()
        self.set_context(VOICE_CONTEXT_IDLE)

    def _preload_model(self) -> None:  # pragma: no cover - dependency/download
        if self._vosk is None:
            return
        try:
            self._ensure_model()
        except Exception as exc:
            self._emit_error_throttled(f"Offline voice model could not be loaded: {exc}")

    def _ensure_model(self):  # pragma: no cover - dependency/download
        cls = type(self)
        if cls._shared_model is not None:
            return cls._shared_model
        if self._vosk is None:
            raise RuntimeError(
                "Vosk is not installed. Install the project requirements before Voice trials."
            )

        with cls._shared_model_lock:
            if cls._shared_model is not None:
                return cls._shared_model
            try:
                if self._model_path:
                    model_path = Path(self._model_path).expanduser()
                    if not model_path.exists():
                        raise FileNotFoundError(
                            f"Configured Vosk model path does not exist: {model_path}"
                        )
                    cls._shared_model = self._vosk.Model(str(model_path))
                else:
                    # Vosk resolves/caches the small model for this language and
                    # downloads it on first use if needed.
                    cls._shared_model = self._vosk.Model(lang=self._language)
                cls._shared_model_error = ""
            except BaseException as exc:
                # Vosk's model resolver may raise SystemExit when it cannot
                # download/find a language model; convert that into an app error.
                cls._shared_model_error = str(exc) or type(exc).__name__
                raise RuntimeError(cls._shared_model_error) from exc
        return cls._shared_model

    def _recognize(self, context: str, clip: dict) -> str:  # pragma: no cover
        model = self._ensure_model()
        grammar = json.dumps(_grammar_for_context(context))
        recognizer = self._vosk.KaldiRecognizer(
            model,
            float(clip["sample_rate"]),
            grammar,
        )
        recognizer.AcceptWaveform(clip["pcm"])
        result = json.loads(recognizer.FinalResult() or "{}")
        return str(result.get("text", "")).strip()

    def _run(self) -> None:  # pragma: no cover - hardware worker
        while not self._shutdown.is_set():
            context = self.context()
            if context == VOICE_CONTEXT_IDLE:
                self._shutdown.wait(0.10)
                continue

            if not self.available:
                self._emit_error_throttled(
                    "Offline voice recognition is unavailable. Install vosk from "
                    "requirements.txt."
                )
                self._shutdown.wait(0.50)
                continue

            try:
                clip = self._microphone.capture_phrase(
                    timeout_s=self._listen_window_s,
                    phrase_time_limit_s=self._phrase_time_limit_s,
                    silence_s=self._silence_s,
                    activation_peak=self._activation_peak,
                )
            except Exception as exc:
                self._emit_error_throttled(f"Microphone phrase capture failed: {exc}")
                self._shutdown.wait(0.25)
                continue

            if clip is None:
                continue
            if self.context() != context:
                continue

            try:
                transcript = self._recognize(context, clip)
            except Exception as exc:
                self._emit_error_throttled(f"Offline voice recognition failed: {exc}")
                continue

            if not transcript:
                continue

            command = parse_voice_command(context, transcript)
            payload = {
                "context": context,
                "transcript": transcript,
                "command": command,
                "timestamp": time.time(),
            }
            self.transcript_heard.emit(payload)

            if command is None:
                continue

            with self._context_lock:
                if self._context != context:
                    continue
                self._context = VOICE_CONTEXT_IDLE

            self.command_recognized.emit(payload)

    def _emit_error_throttled(self, message: str) -> None:
        now = time.monotonic()
        if now - self._last_error_emit < 8.0:
            return
        self._last_error_emit = now
        logger.warning(message)
        self.recognition_error.emit(message)
