# Study 2 Fullscreen Participant Window — v1.8.4

## Change
Actual Study 2 participant-facing Gridworld trials now open fullscreen by default regardless of whether the HINT console is running in `STUDY` or `DEVELOPMENT` mode.

### Start Activity gate
- For non-practice Study 2 trials, the participant Start Activity gate is shown fullscreen.
- This matches the Study 1 fullscreen behavior introduced in v1.8.3.

### After START ACTIVITY
- The Gridworld remains fullscreen when the Study 2 trial begins.

### Training / practice
Training and practice sessions keep the previous DEVELOPMENT-mode windowed behavior. Existing STUDY-mode fullscreen behavior is preserved.

### Fullscreen toggle
Press `F11` in the participant window to toggle fullscreen/normal mode for testing.

### Beam recording
The automatic Beam participant-display capture from v1.8.2 is preserved. The monitor is resolved from the actual participant window before recording starts, so fullscreen placement and gaze-overlay recording stay aligned.
