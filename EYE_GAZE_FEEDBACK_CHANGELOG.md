# V1.1.6 — HoloLens 2 Eye Gaze feedback

This version adds live eye-gaze feedback to the Actor-Critic Gridworld using the
HoloLens 2 Extended Eye Tracking (EET) stream that the console already records.
No second HoloLens connection is opened.

## Study protocol implemented

### System-requested feedback

1. The RL system detects a critical state and pauses for feedback.
2. The participant keeps looking at the maze/state briefly; this becomes the
   local gaze center for that feedback request.
3. The participant looks **UP, DOWN, LEFT, or RIGHT twice**, returning gaze to
   center between the two looks.
4. Two matching looks are converted to the corresponding human action and sent
   through the same `RLManager.submit_feedback()` path used by Keyboard/Voice.
5. The action is executed immediately, including the existing collision/reset
   behavior fixed in v1.1.5.

### Anytime feedback

1. While the agent is moving, **two short blinks** trigger Pause/Select Feedback.
2. The numbered recent-state display appears.
3. The participant **closes both eyes for about one second**, then opens them.
   This long closure acts as a delimiter so ordinary pause blinks cannot be
   confused with the state number.
4. The participant blinks **N times** to select state box **N**. The count is
   finalized after the eyes remain open for the configured quiet gap.
5. The chosen state is highlighted.
6. The participant looks in the corrective direction **twice**, returning gaze
   to center between looks.
7. Feedback is applied to the selected historical step and live training resumes.

## Blink detection

The current HL2SS/HoloLens 2 path does not expose a dependable eyelid-openness
signal. The recognizer therefore treats a short interval in which combined,
left, and right gaze rays are all invalid as a blink. It only interprets such an
interval when EET calibration itself is valid, reducing the chance that a lost
tracking/calibration state becomes a command.

Default timing thresholds are in `config/study.yaml` under
`eye_gaze_recognition` and can be tuned without code changes:

- normal blink: 0.06–0.45 s
- long eye close: >= 0.85 s
- two-blink pause window: 1.10 s
- blink-count completion gap: 1.15 s

## Direction recognition

Direction is computed from the combined EET gaze ray. Instead of using headset
optical forward as an absolute center, the recognizer captures the participant's
current fixation at the start of each direction-feedback stage. A look must move
at least 12 degrees from that local center and must return within 6 degrees of
center before another look can count. Two matching looks within 1.8 seconds
produce the direction command.

These thresholds are configurable in `config/study.yaml`:

- `direction_threshold_deg`
- `direction_neutral_deg`
- `direction_dominance_margin_deg`
- `double_look_window_seconds`
- `center_adaptation_alpha`

## Study/Training integration

- Study 2 Training: Gridworld + Eye Gaze is now a live Actor-Critic trial.
- Study 2 Study: the fourth required modality is now explicitly **Eye Gaze**
  instead of the previous `Implicit (Gaze/Physiological)` placeholder.
- Existing v1.1.5 Study 2 rows labeled `Implicit (Gaze/Physiological)` are still
  counted as historical Eye Gaze completion for backward compatibility.
- Study 1 Training already contains Eye Gaze in its 2 x 4 practice matrix; the
  new participant-window recognizer makes that condition functional as well.
- Eye-gaze trials require a live HoloLens EET stream and valid eye calibration
  before the run can start.

## Logging

Raw EET continues to be stored under `sensors/hololens/`. In addition, recognized
interaction events are written to `events/events.csv` as:

- `GAZE_GESTURE` — individual blink/direction-look progress and long-close events
- `GAZE_COMMAND` — accepted double-blink, blink-count, and double-look commands

Accepted RL interventions continue to be recorded by the existing RL recorder
with modality `Eye Gaze`.

## Validation

The hardware-independent test suite includes checks for:

- double-blink pause recognition;
- one-second long eye closure;
- N-blink state-number selection;
- all four double-look directions;
- requiring a center return between direction looks;
- rejecting uncalibrated tracking loss as a blink;
- requested-feedback collision/reset behavior with Eye Gaze modality.
