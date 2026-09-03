# Study 1 Fullscreen Participant Windows — v1.8.3

## Change
Actual Study 1 participant-facing environments now open fullscreen by default regardless of whether the HINT console is running in `STUDY` or `DEVELOPMENT` mode.

### Gridworld
- The participant Start Activity gate is shown fullscreen for non-practice Study 1 trials.
- The window remains fullscreen when the Gridworld trial begins.

### Continuous room
- The participant Start Activity gate is shown fullscreen for non-practice Study 1 continuous-navigation trials.
- The window remains fullscreen after the Ubuntu task reports that it has started.

### Training / practice
Training remains windowed in DEVELOPMENT mode so researchers can debug and configure the system conveniently. Existing STUDY-mode behavior is preserved.

### Fullscreen toggle
Press `F11` in either participant environment to toggle fullscreen/normal mode for testing.

### Beam recording
The v1.8.2 automatic Beam participant-display detection is preserved. Because Beam resolves the monitor from the actual participant window immediately before activity recording begins, fullscreen placement and screen recording remain aligned.
