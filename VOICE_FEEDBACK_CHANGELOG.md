# V1.1.4 — Study 2 Voice Feedback

## Scope

Voice feedback is now a real live Actor-Critic Gridworld input path in both:

- **Study 2 — Training** (Gridworld + Voice)
- **Study 2 — Study** (Voice modality)

The microphone selected/connected on the **Devices** page is reused for command recognition.

## System-requested feedback

1. The Actor-Critic system detects an ambiguous/critical state and pauses.
2. The participant window asks for feedback.
3. The participant says **UP**, **DOWN**, **LEFT**, or **RIGHT**.
4. The recognized action is logged as `Voice`.
5. Human guidance is applied to Actor-Critic.
6. The chosen action is also executed immediately as a real Gridworld transition, so the agent actually moves in the spoken direction.
7. Training resumes unless that action ended the episode.

The existing requested-feedback timeout remains active.

## Anytime feedback

1. During normal movement the recognizer waits for **STOP**.
2. Saying **STOP** pauses movement and opens the existing recent-state history (up to 10 boxes).
3. The participant says the box number (**one** through **ten** / 1 through 10).
4. The selected historical state is highlighted.
5. The participant says **UP**, **DOWN**, **LEFT**, or **RIGHT**.
6. Human guidance is applied to that selected historical state.
7. As in the existing Anytime design, the live environment is **not rewound**; it resumes from the pause state.
8. The recognizer returns to listening for **STOP**.

Mouse/keyboard direction input is blocked during Voice conditions so keyboard actions cannot be mislabeled as voice feedback.

## Offline speech recognition

- Backend: **Vosk 0.3.45**.
- Recognition uses a constrained grammar for the current phase (`STOP`, state numbers, or directions).
- Audio is captured through the already-open `sounddevice` microphone stream.
- Recognition runs locally on the study PC.
- `config/study.yaml` contains Vosk language/model settings and microphone phrase-detection thresholds.
- If `model_path` is blank, Vosk resolves its small English model by language and may download/cache it on first initialization. For a study PC with no network access, pre-download the model and set `model_path`.

## Logging

Two event types were added to `events/events.csv`:

- `VOICE_TRANSCRIPT` — recognition context, raw transcript, parsed command
- `VOICE_COMMAND` — accepted context/command/transcript

Applied feedback continues to be written to `rl/interventions.csv` with `modality=Voice`. Requested human actions also appear as actual transitions in `rl/rl_steps.csv`.

## Dependencies

`requirements.txt` now includes:

```text
vosk==0.3.45
```

Run `pip install -r requirements.txt` in the HINT conda/venv before using Voice feedback.
