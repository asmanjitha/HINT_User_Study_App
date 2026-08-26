# Study 1 condition-matrix update (v0.4)

Study 1 experimental collection now requires all eight combinations of:

- Feedback timing: Requested Feedback, Anytime Feedback
- Modality: Keyboard, Joystick, Voice, Eye Gaze

## Researcher workflow

The **Study 1 — Study** panel now contains a 2 x 4 status matrix. Each cell is one required condition and displays:

- `Not Started`
- `In Progress`
- `Completed`
- `Needs Repeat` (a stopped/aborted attempt exists but no completed trial exists)

Clicking a cell selects that timing/modality pair in the run configuration. **Select Next Incomplete** jumps to the next condition that still needs collection. Re-running an already completed condition is allowed only after a confirmation prompt.

The left workflow menu now shows Study 1 progress as `x/8 conditions`. Study 1 remains **In Progress** until all eight unique conditions have at least one completed experimental trial. Practice/training trials do not count.

The Study 1 run-history table now records the feedback timing and modality beside each run.

## Data source

Condition status is reconstructed from the persisted `trials` table in `experimental.sqlite3`, using:

- participant code
- Study 1
- Gridworld environment
- `practice = 0`
- feedback timing
- modality
- trial status

This means closing and reopening the console does not lose the matrix status.

## Important implementation boundary

This update adds the **study-condition scheduling and completion tracking** for all four modalities. The current repository still only contains keyboard-based participant input for the Actor-Critic Gridworld; real joystick, voice-recognition, and HoloLens eye-gaze input adapters are not implemented in this build. Do not treat a non-keyboard workflow test as valid modality data until those input adapters are connected.

## Tests

`tests/test_study1_condition_matrix.py` verifies:

1. The matrix contains exactly 2 x 4 = 8 required conditions.
2. A new participant starts with all eight conditions Not Started.
3. Completing one trial completes only its exact timing/modality pair.
4. A stopped trial becomes Needs Repeat.
5. Seven completed conditions leave Study 1 In Progress.
6. All eight completed conditions mark Study 1 Completed.

## Workflow safeguard

Study 2 start controls are disabled until the participant's Study 1 experimental matrix reaches 8/8. A second guard in the Study 2 start action also blocks manual attempts to start Study 2 early.
