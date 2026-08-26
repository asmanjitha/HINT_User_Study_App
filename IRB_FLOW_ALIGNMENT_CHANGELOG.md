# HINT Console v0.6 — IRB Study Flow Alignment

## What changed

### Study 1 — Study
The experimental Study 1 panel no longer uses the old `2 timings x 4 modalities` matrix.
It now follows three main protocol settings:

1. **2D Gridworld HIL-RL**
   - System-requested feedback
   - Anytime feedback
   - Explicit input only: Keyboard or Joystick
2. **Indoor room navigation**
   - Explicit input only: Keyboard or Joystick
   - Requested/Anytime timing is recorded per run
3. **Baseline**
   - Experimenter navigates virtually
   - No participant feedback modality (`N/A`) is recorded

This produces **4 required Study 1 protocol conditions** in the progress tracker because the
Gridworld setting has two required feedback-timing conditions.

### Study 1 — Training
The existing training/familiarization window is intentionally unchanged. It keeps the 2 x 4
practice matrix so the current training workflow is preserved.

### Study 2 — Study
Study 2 now emphasizes **multimodal feedback in the 2D Gridworld**. The required modality
tracker is:

- Keyboard
- Joystick
- Voice
- Implicit (Gaze/Physiological)

Feedback timing (Requested/Anytime) is recorded for the run, but completion is tracked by
modality so the UI emphasizes the modality-comparison purpose of Study 2.

## Backend/integration status

The existing live Actor-Critic Gridworld path is currently a real **Keyboard** integration.
Joystick, voice, gaze/physiological, continuous-room, and experimenter-baseline integrations
are not implemented as live device/simulator adapters in this build. For these conditions the
console creates a **tracked Trial** with the normal session/trial folder, metadata, timestamps,
status, and DB records, but does not falsely relabel keyboard input as another modality.

## Protocol note

The supplied IRB currently states that Study 2 experiments are conducted in all three settings
(Gridworld, room environment, and human-agent navigation). v0.6 makes Gridworld the primary
Study 2 experimental condition tracker per the requested GUI focus. If Study 2 is intended to be
Gridworld-only in the actual data collection, the IRB wording should be updated accordingly.
