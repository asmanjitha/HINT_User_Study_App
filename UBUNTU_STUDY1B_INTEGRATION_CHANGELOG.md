# HINT Console v1.2.0 — Ubuntu Study 1(b) Integration

## Scope

This release maps **Study 1(b): Continuous Action-Space Room Navigation** to the
Ubuntu `HINT_ContinuousNav_Ubuntu_v1` worker while retaining the current HINT
Console's HoloLens, Shimmer, participant/session/trial, and readable R## data
collection architecture.

## Runtime ownership

- **Ubuntu worker:** GA3C process, Simulation2D state, collision detection,
  rewind snapshot, N human-control steps, human transitions, RL resumption.
- **Windows HINT Console:** participant/session/trial identity, HoloLens PV/EET,
  Shimmer GSR/PPG, selected human input modality, participant-facing room view,
  master event timeline, Ubuntu bundle collection.

The simulator state is never recreated on Windows. Windows only renders a copy
of the static room geometry and the streamed robot/goal pose.

## New files

- `remote/continuous_nav_client.py` — persistent worker connection, command/
  event protocol, clock offset measurement, synchronized Console-side logs,
  trial bundle download/verification.
- `gui/continuous_nav_window.py` — participant-facing room renderer and
  lock-step Keyboard/Joystick feedback input.
- `tests/test_continuous_nav_client.py` — console timestamp/state-log and
  worker-v1 checksum compatibility tests.

## Modified behavior

`Study 1 — Study -> room_navigation` is no longer a tracked placeholder.
It launches `ApplicationController.start_continuous_room_trial()` and uses
`rl_algorithm="ubuntu_ga3c_continuous_room"` in trial metadata.

The room condition is now explicitly **Requested Feedback**, matching the
actual Ubuntu mechanism: feedback is requested after collision/rewind.

## Trial synchronization order

Start:

1. Create HINT T##/R## trial.
2. Set the Console-side remote log paths.
3. Send `PREPARE_TRIAL` to Ubuntu and receive room geometry.
4. Start HINT trial lifecycle.
5. Start HoloLens and Shimmer trial recording.
6. Send `START_TRIAL` to Ubuntu.

Stop:

1. Send `STOP_TRIAL` or `ABORT_TRIAL` to Ubuntu.
2. Publish HINT `TRIAL_ENDED` and stop sensors.
3. Send `FINALIZE_TRIAL`.
4. Download `GET /trial/bundle` to `R##/rl/ubuntu/`.
5. Extract and SHA-256 verify immutable worker-v1 files.

## Human action mapping

Ubuntu action IDs are unchanged:

| ID | Action |
|---:|---|
| 0 | Sharp left |
| 1 | Medium left |
| 2 | Slight left |
| 3 | Straight |
| 4 | Slight right |
| 5 | Medium right |
| 6 | Sharp right |

Keyboard mapping matches the original Ubuntu Tk popup: W/S, A/D, Q/E,
Shift+A/Shift+D, Esc to skip. Joystick X deflection maps to the six turn bins;
forward Y maps to straight.

## Validation performed

- `python -m compileall .` succeeds.
- Full Console test suite: **91 passed**.
- Live worker protocol smoke test: Connect, GET_STATUS, five PING/PONG clock
  samples, PREPARE_TRIAL room geometry, FINALIZE_TRIAL, bundle download and
  extraction succeeded.
- Lock-step bridge smoke test succeeded:
  `HUMAN_ACTION_REQUEST -> Console action 5 -> ACTION_RESPONSE(status=received)`.

The full GA3C/TensorFlow run still needs to be exercised on the actual Ubuntu
study PC because this packaging environment is not the user's configured GA3C
runtime/GPU environment.
