# HINT Study Console V1.3.0 — Revised Study Flow

## Visible workflow

`Registration -> Training Phase -> Study 1 (When) -> Study 2 (How) -> Agent Observation`

The legacy `Study 2 - Training` enum/database value is retained only for backward compatibility and is no longer shown as a workflow page.

## Training Phase

Required practice conditions (6):

1. Gridworld — Requested — Keyboard
2. Gridworld — Anytime — Keyboard
3. Continuous Room — Requested — Keyboard
4. Continuous Room — Anytime — Keyboard
5. Gridworld — Requested — Joystick
6. Gridworld — Requested — Voice

Optional final item:

- HoloLens familiarization / sensor check — no feedback

Anytime practice is Keyboard-only. Quick Pass marks only the six required conditions and does not create fake Trial rows.

## Study 1 — When should a human intervene?

Study 1 is Keyboard-only and requires four valid conditions:

- Gridworld Requested
- Gridworld Anytime
- Continuous Room Requested
- Continuous Room Anytime

The previous baseline condition and Study-1 Joystick option no longer count toward completion.

## Study 2 — How should a human provide feedback?

- Environment: Gridworld only.
- Timing: researcher chooses Requested or Anytime.
- Modalities: Keyboard, Joystick, Voice.
- All three are connected to the live Actor-Critic Gridworld path.
- HoloLens/Eye Gaze is not a Study 2 feedback modality.
- Study 2 does not require every modality. After at least one valid modality trial, **Finish Study 2 & Continue** writes an explicit completion marker and advances the GUI to Agent Observation.

Joystick feedback supports both timing modes. Requested mode submits the dominant joystick axis as Up/Down/Left/Right. Anytime mode uses joystick button 1 to pause, horizontal stick movement to select a recent-state box, button 1 to confirm, and the next directional tilt as the correction.

## Agent Observation Phase

Two valid no-feedback runs are required:

- Gridworld — agent learning without human feedback
- Continuous Room — agent learning without human feedback

Before each run, the Console requires:

- fresh Shimmer GSR/PPG stream
- fresh HoloLens stream

The standard trial sensor lifecycle then records Shimmer and HoloLens data into the selected `R##` folder.

Data root:

`Phase3_AgentObservation_NoFeedback/T##_.../R##/`

Observation trial IDs use `..._OBS_T##_R##`.

## Continuous Room protocol extension

Console-side support was added for Continuous Anytime via a new outbound worker message:

`BEGIN_ANYTIME_FEEDBACK`

The Ubuntu worker is not included in this Console ZIP. It must understand this message for Continuous Anytime to function end-to-end.

For no-feedback Agent Observation, the Console prepares the worker with feedback mode `N/A` and modality `N/A (No Participant Feedback)`. A compatible worker should bypass collision rewind/human-action requests entirely. The Console auto-skips any unexpected human-action request defensively, but true no-feedback behavior should be implemented worker-side.

## Validation

- Python compilation: passed.
- Protocol/unit tests: 98 passed.
- GUI runtime smoke test could not be executed in the packaging environment because PySide6 is not installed there; PySide6 remains listed in `requirements.txt` and all GUI modules compile successfully.
