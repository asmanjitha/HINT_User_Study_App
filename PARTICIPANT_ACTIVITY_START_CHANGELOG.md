# HINT Study Console v1.5.0

## Participant-controlled activity start

- Researcher-side Start now prepares the Trial and opens the appropriate
  participant window without starting protocol time or the task.
- Every participant window presents an opaque, full-window **START ACTIVITY**
  confirmation page.
- The participant click starts the persisted Trial timestamp, the main-window
  study countdown, sensor recordings, and the selected Gridworld or Ubuntu
  continuous-navigation backend.
- The researcher-console timer reports that it is waiting for the participant
  during the prepared state. Timers remain absent from participant windows.
- Prepared activities can still be marked valid, invalid/repeat, or aborted by
  the researcher before the participant clicks Start.
- New lifecycle events: `ACTIVITY_PREPARED` and
  `PARTICIPANT_ACTIVITY_STARTED`.

Protocol countdown durations remain configurable in `config/study.yaml`:
Study 1 = 8 minutes, Study 2 = 8 minutes, and Observation = 5 minutes by
default. Training remains untimed.
