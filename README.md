# HINT Study Console

**HINT — Understanding When and How Humans Should Intervene in
Reinforcement Learning.** A researcher-facing desktop application for
running the HINT human-in-the-loop RL user study.

## What's in this version (V1.1.4 — Study 2 offline voice feedback)

The console uses a single process-oriented workflow on the existing data
model and Actor-Critic Gridworld integration. V0.9 retains the IRB-aligned
Study panels and adds readable condition/run folder naming so collected data
can be identified directly from the filesystem:

    Devices  ->  Workflow  ->  Event Log

- **Devices** — Microsoft HoloLens 2, Classic Shimmer3 GSR+, physical keyboards,
  joystick/gamepad, and microphone all have real connection panels. HoloLens 2
  uses the official HL2SS client to keep the PV/front RGB camera and Extended
  Eye Tracking streams live over the local network. After the first successful
  connection, a separate validation window opens once to show live camera video
  plus combined/left/right gaze rays; **Validate Connection** reopens it later.
  Shimmer retains its guided Bluetooth COM workflow, live GSR+PPG verification,
  and trial CSV recording. **HoloLens recording now follows every persisted
  Training and Study trial:** each `R##` stores an annotated PV video, a
  frame-synchronized gaze-pointer CSV, the raw EET packet CSV, and recording
  metadata under `sensors/hololens/`.

- **Workflow** — register a participant (name, age, email) or select an
  existing one, then step through the study using a left-hand menu:

  1. Registration
  2. Study 1 — Training
  3. Study 1 — Study
  4. Study 2 — Training
  5. Study 2 — Study

  Each step shows its status at a glance (⬜ Not Started / 🔶 In Progress /
  ✅ Completed) and, for the four repeatable steps, how many times it's
  been run. Click any step to jump straight to it — finished or
  unfinished — configure/monitor a run, and press **Start** to begin a new
  run (repeats create a new run rather than overwriting the last one, so
  "do training three times" and "redo the study a second time" both just
  work). Study 1 Training keeps its existing practice matrix. Study 1 Study
  now tracks the three protocol settings (with two Gridworld timing
  conditions), and Study 2 Study centers its progress view on multimodal
  Gridworld feedback. Keyboard **and Voice** Gridworld runs now launch the live
  Actor-Critic integration in both Study 2 Training and Study 2 Study. Voice
  uses the microphone selected on the Devices page and local Vosk speech
  recognition. Joystick/implicit conditions remain tracked until their dedicated
  live adapters are connected.

- **Event Log** — live event feed + disk usage, for monitoring/debugging.
  Device status and current-session context now live on the Devices and
  Workflow pages instead.

The participant-facing second window (maze view + feedback controls) opens
automatically for live Keyboard and Voice Gridworld trials. In Voice Requested
Feedback, the participant says **UP / DOWN / LEFT / RIGHT**. In Voice Anytime
Feedback, the participant says **STOP**, then the number of one of the displayed
recent-state boxes, then **UP / DOWN / LEFT / RIGHT**.

Voice recognition is local through Vosk. If no English Vosk model is already
cached, Vosk can obtain its small English model on first initialization; for a
fully offline study machine, set `voice_recognition.model_path` in
`config/study.yaml` to a pre-downloaded model directory before data collection.

### HoloLens files per activity run

The existing naming convention is unchanged. For example:

```text
data/P001/S01/Training/Study1/
  TR01_Gridworld_Anytime_Gaze/R01/
    sensors/hololens/
      hololens_pv_gaze_overlay.mp4
      hololens_gaze_pointer.csv
      hololens_eet_raw.csv
      hololens_recording_metadata.json

data/P001/S01/Study1_ExplicitFeedback/
  T02_Gridworld_Anytime_Joystick/R01/
    sensors/hololens/
      ...same four files...
```

A repeated collection uses the next existing run directory (`R02`, `R03`, ...),
so camera/gaze recordings are never intentionally merged across attempts.

### Why this design

- **One flow, not eight tabs.** The researcher's actual job — register
  someone, then walk them through training and the study, possibly more
  than once — is now the literal navigation structure of the app, instead
  of being reconstructed by hand from a generic Participants + Study Setup
  + Live Session page every time.
- **Runs, not just sessions.** A new `workflow_runs` table (see
  `core/workflow_manager.py`) sits alongside the existing session/trial
  tables and records one row per *attempt* at a step. A step's displayed
  status is an aggregate over all of its runs, so repeats are first-class
  rather than something the researcher has to track by re-reading session
  IDs.
- **Nothing about the working parts changed.** `SessionManager`,
  `TrialManager`, `RLManager`, the Actor-Critic Gridworld experiment, the
  event bus, the two-database PII split, and the on-disk
  session/trial folder structure are all exactly as they were in V0.1.
  `WorkflowManager` is a thin layer on top: every "run" is a real
  `Session`, created and started the normal way.

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Requires Python 3.11+. PySide6 provides Qt 6 bindings.

## Running

```bash
python main.py
```

On first launch this creates `data/identifiable.sqlite3` and
`data/experimental.sqlite3`, and starts logging to `logs/system.log`.

To run headlessly (e.g. in CI or over SSH with no display), set:

```bash
QT_QPA_PLATFORM=offscreen python main.py
```

## Running tests

```bash
pytest tests/ -v
```

## Architecture

```
hint_study_console/
├── main.py                    # entry point
├── core/                      # non-GUI application services
│   ├── config_loader.py       # loads/validates config/*.yaml
│   ├── logging_setup.py       # Python logging -> logs/system.log
│   ├── event_bus.py           # pub/sub for StudyEvents (Qt signal based)
│   ├── database.py            # SQLite schema + connections
│   ├── id_generator.py        # participant/session/trial/run ID generation
│   ├── participant_manager.py # create/search participants; owns PII split
│   ├── session_manager.py     # session lifecycle + on-disk folder creation
│   ├── trial_manager.py       # trial lifecycle within a session
│   ├── workflow_manager.py    # NEW: step/run tracking on top of sessions
│   └── application_controller.py  # wires the above (+ rl/device managers) together
├── models/                    # plain dataclasses/enums, no Qt dependency
│   ├── enums.py                # DeviceStatus, SessionStatus, WorkflowStep, ...
│   ├── event.py, participant.py, session.py, trial.py, workflow.py
├── devices/                   # device interfaces + hardware adapters
│   ├── base_device.py          # BaseDevice interface, MockDevice
│   ├── shimmer_protocol.py     # Shimmer3 LiteProtocol helpers + packet parser
│   ├── shimmer_device.py       # real Bluetooth/serial GSR+PPG streaming
│   ├── input_devices.py        # keyboard/joystick/microphone adapters + PCM phrase capture
│   ├── voice_recognizer.py     # local Vosk STOP/number/direction recognition
│   └── device_manager.py       # owns one device per DeviceType
├── gui/                       # PySide6 widgets
│   ├── main_window.py          # left nav (Devices / Workflow / Event Log)
│   ├── devices_page.py, device_status_strip.py
│   ├── workflow_page.py        # participant selector + step menu + detail panels
│   ├── registration_panel.py   # Registration step detail
│   ├── study1_step_panel.py    # Study 1 Training/Study (real RL trial)
│   ├── study2_step_panel.py    # Study 2 Training; live Gridworld Keyboard/Voice
│   ├── participant_dialog.py   # "New Participant" dialog
│   ├── participant_window.py   # participant maze + keyboard/voice feedback state machine
│   └── event_log_page.py
├── rl/, recording/            # Actor-Critic Gridworld experiment + CSV recorder (unchanged)
├── config/                    # app.yaml, study.yaml, logging.yaml
├── tests/                     # pytest coverage (protocol tests are GUI-free)
└── data/                      # session data root (gitignored contents)
```

Design principles carried through from V0.1:
- **GUI never touches core logic directly** — pages call manager methods
  and subscribe to the event bus; core code never imports from `gui/`.
- **PII lives in one place.** `ParticipantManager` is the only code that
  writes to `data/identifiable.sqlite3`. Age is study-relevant but not
  identifying on its own, so it's stored as a `demographics` field on the
  pseudonymous experimental record — never alongside name/email/phone.
  Everything else — sessions, trials, workflow runs, the filesystem, RL,
  recording — only ever sees a pseudonymous `participant_code` like `P023`.
- **Session-centric, not device-centric.** One participant collection visit
  reuses the same active `S##` session across Study 1 and Study 2. Individual
  study conditions are `T##` (or `TR##` for training), and repeated attempts
  are `R01`, `R02`, ... without overwriting earlier data.

## Data directory structure and naming convention

The on-disk hierarchy is human-readable while the SQLite database keeps a
unique immutable `trial_id` for joins. For experimental data:

```text
data/
└── P001/
    └── S01/
        ├── session.json
        ├── configuration.yaml
        ├── Study1_ExplicitFeedback/
        │   └── T02_Gridworld_Anytime_Joystick/
        │       ├── R01/   # first attempt (can be Invalid)
        │       │   ├── trial.json
        │       │   ├── rl/
        │       │   ├── sensors/
        │       │   ├── input/
        │       │   ├── events/
        │       │   └── logs/
        │       └── R02/   # repeated attempt of the exact same condition
        └── Study2_MultimodalFeedback/
            └── T01_Gridworld_Requested_Voice/
                └── R01/
```

Training/familiarization data uses a separate namespace:

```text
P001/S01/Training/Study1/TR01_Gridworld_Requested_Keyboard/R01/
P001/S01/Training/Study2/TR01_Gridworld_Requested_Voice/R01/
```

Naming rules:

- `P###` — participant code.
- `S##` — participant collection session.
- `T##` — one exact experimental condition within a study.
- `TR##` — one exact training/familiarization condition.
- `R##` — one concrete collection attempt of that condition.
- Repeating the same environment + timing + modality reuses the same `T##`
  folder and automatically creates the next `R##`.
- A different environment, timing, or modality gets a new `T##`.
- Study 1 and Study 2 have their own `T##` numbering because they live in
  separate study folders.

The Study 1 and Study 2 GUI panels show the **next data folder** before a run
starts, then show the **current data folder** while it is running. Run History
shows Condition (`T##`), Attempt (`R##`), and the collection result.

Each experimental run can be ended as **Valid**, **Invalid**, or **Aborted**.
Invalid/aborted data are preserved; they do not satisfy the condition-completion
tracker. Choosing **Mark Invalid / Repeat** stores the reason and the next
collection of the same condition becomes `R02`, `R03`, etc.

Identifiable information (name/email/phone) is never written inside a session
folder — see `data/identifiable.sqlite3`, which is entirely separate from
`data/experimental.sqlite3` (participant/session/trial/workflow metadata, no
PII).

## Configuration

Three YAML files in `config/`:

- **`app.yaml`** — application mode (`DEVELOPMENT` / `STUDY`), data/log
  paths, backup destination (intentionally blank; configure per deployment,
  never hard-code a university path).
- **`study.yaml`** — the study protocol: session/task time limits, study
  definitions, critical-state parameters, random seed. This file is
  snapshotted into every session folder as `configuration.yaml`.
- **`logging.yaml`** — standard `logging.config.dictConfig` format.

## Shimmer connection workflow

1. Attach the GSR electrodes and optical PPG probe and power on the Shimmer.
2. Pair the classic Shimmer3 in Windows Bluetooth so an RFCOMM/virtual COM
   port exists.
3. Open **Devices**, click **Refresh Ports**, select the Shimmer COM port, and
   click **Connect & Start Streaming**.
4. Do not start participant collection until the panel reports **Connected and
   receiving live GSR + PPG data**.
5. Use **Check Live Data** at any time to verify that new samples are still
   reaching the GUI.

The continuous connection-level diagnostic CSV is saved under
`data/device_logs/shimmer/`.

For every **experimental** Study 1 or Study 2 trial, the console now automatically
starts a second trial-scoped physiological recording while keeping the Bluetooth
stream running. The primary study files are saved as:

```text
P001/S01/Study1_ExplicitFeedback/T02_Gridworld_Anytime_Joystick/R01/
    sensors/shimmer_gsr_ppg.csv
    sensors/shimmer_recording_metadata.json
```

The trial CSV includes participant/session/trial identifiers plus
`condition_code`, `run_code`, and `condition_name`, along with host timestamps,
trial-relative time, Shimmer timestamps, raw GSR/ADC/range, and raw optical PPG.
Practice/training trials are intentionally excluded from the primary Shimmer
study recording path.

## How to add another real device

Implement a class in `devices/` that subclasses `BaseDevice` (see
`devices/base_device.py`) and register it in `DeviceManager.__init__`. The current
HoloLens, Shimmer, keyboard, joystick, and microphone adapters demonstrate the
shared connection/status pattern.

## How to wire up non-Keyboard Study 2 modalities

`gui/study2_study_panel.py` launches the live Actor-Critic Gridworld for
Keyboard and calls `controller.start_tracked_trial(...)` for modalities whose
real adapter is not connected yet. Replace that tracked path with the real
modality/device adapter when Joystick, Voice, or HoloLens/physiological input
is implemented; the WorkflowManager completion logic and trial metadata do
not need to change.

## Known limitations (V0.9)

- HoloLens 2 connection/validation is integrated through HL2SS for PV camera +
  Extended Eye Tracking, but HoloLens trial recording is not yet automatically
  written into each trial folder. This milestone verifies the live streams.
- Shimmer is integrated for the classic Shimmer3 Bluetooth virtual-COM/RFCOMM
  workflow used by the official Python scripts.
- Shimmer hardware cannot be physically exercised in automated tests; run the
  guided connection workflow on the study Windows workstation before use.
- The live Actor-Critic Gridworld feedback adapter is Keyboard-only. It can be
  launched from Study 1 Gridworld and Study 2 Keyboard conditions.
- The input devices and HoloLens can now be connected/verified, but routing each
  modality into its final Study 1/2 trial recorder still remains a separate
  implementation layer. Indoor-room and baseline task adapters also remain to be
  connected to their final task implementations.
- `STUDY` mode config locking is read but not yet enforced in the UI.
- Session/task time-limit **enforcement** (vs. display) is not yet wired
  into the workflow steps.

## IRB-aligned study flow (v0.6)

- **Study 1 — Training:** existing 2 x 4 familiarization matrix remains unchanged.
- **Study 1 — Study:** 4 required protocol conditions across three settings: Gridworld Requested, Gridworld Anytime, Indoor Room explicit feedback, and Experimenter Baseline. Experimental Study 1 uses Keyboard/Joystick only.
- **Study 2 — Training:** existing training/session-tracking window remains in place.
- **Study 2 — Study:** Gridworld-focused multimodal tracker with Keyboard, Joystick, Voice, and Implicit (Gaze/Physiological) conditions.

The live integrated RL path is currently Actor-Critic Gridworld + Keyboard. Non-integrated modalities/environments use tracked Trials rather than being silently simulated with keyboard input. See `IRB_FLOW_ALIGNMENT_CHANGELOG.md`.


## Selectable keyboard, joystick, and microphone hardware (v1.0)

The Devices page now enumerates and connects the actual study input hardware instead of using placeholder adapters:

- **Keyboard:** choose one required physical keyboard and optionally a second keyboard (maximum two). On Windows the list comes from Raw Input hardware identities.
- **Joystick/gamepad:** choose one SDL/pygame joystick and keep it open/polled while connected.
- **Microphone:** choose one PortAudio input device; the console opens a monitoring input stream and only reports **Receiving Data** after audio frames reach the application.

Every panel includes **Refresh Devices**, **Connect**, **Check Connection**, and **Disconnect** controls. The microphone panel also shows a live input-level meter. Install the new dependencies with `pip install -r requirements.txt`.

This v1.0 milestone connects and verifies the devices. Device-specific study recording for keyboard/joystick/microphone is a separate next layer; the selected hardware identities are now exposed through `DeviceManager` so they can be routed into the existing `P###/S##/Study.../T##.../R##` recording structure.


## Microsoft HoloLens 2 + HL2SS (v1.1)

The Devices page now contains a guided **Microsoft HoloLens 2 — Eye Gaze + PV
Camera** panel. Download/install the upstream HL2SS server app on the headset,
download the HL2SS repository on the study PC, and select its repository root or
`viewer` folder in the console. The HoloLens and PC must be reachable on the same
network.

The console streams:

- PV/front RGB video (default 1280x720 @ 30 FPS), and
- Extended Eye Tracking at 30/60/90 Hz (default 60 Hz).

After both streams deliver data, the device status becomes **Receiving Data** and
a separate validation window opens once. That window shows live video, eye
calibration validity, and combined/left/right gaze ray origins and directions.
Closing the window leaves the streams connected; **Validate Connection** checks
packet freshness and reopens it at any time.

See `HOLOLENS2_CONNECTION_CHANGELOG.md` for the complete setup and behavior.
