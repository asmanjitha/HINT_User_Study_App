# HINT Study Console

**HINT — Understanding When and How Humans Should Intervene in
Reinforcement Learning.** A researcher-facing desktop application for
running the HINT human-in-the-loop RL user study.

## What's in this version (V1.2.2 — Study 1 Training Quick Pass + Ubuntu continuous navigation)

- **Study 1(b) Continuous Action-Space Room Navigation is now live-integrated.** The existing `Continuous Room Navigation` placeholder now connects to the Ubuntu `HINT_ContinuousNav_Ubuntu_v1` worker over WebSocket/HTTP.
- The Windows Console remains authoritative for participant/session/trial IDs, HoloLens recording, Shimmer recording, participant input, and master events. Ubuntu remains authoritative for GA3C, simulator state, collision detection, rewind snapshots, N-step human control, and RL continuation.
- Study 1(b) uses the collision-triggered **Requested Feedback** mechanism: collision → rewind `N` steps → request exactly one action for each restored human-control state → apply `N` human steps → resume RL from the final human-controlled state.
- A new participant-facing room window renders the Ubuntu `.world` geometry locally and streams the current robot pose and goal. No VNC/screen capture is required.
- Keyboard controls match the original Ubuntu popup: `W/S` straight, `A/D` medium turn, `Q/E` slight turn, `Shift+A/Shift+D` sharp turn, `Esc` skip. A selected joystick can also provide the seven discrete steering actions.
- The Study 1 researcher panel now includes Ubuntu worker IP/hostname, **Connect / Test**, measured median network RTT / clock offset, rewind-control `N`, and feedback timeout.
- Trial start order is synchronized: Ubuntu `PREPARE_TRIAL` → start HoloLens/Shimmer → Ubuntu `START_TRIAL`. Trial stop reverses this: stop Ubuntu task → close HINT trial/sensors → finalize/download Ubuntu data.
- Console-side remote logs add `console_receive_timestamp_utc_ns` to every Ubuntu event and save `continuous_nav_state_stream.csv` and `continuous_nav_actions.csv`.
- Ubuntu's finalized ZIP is downloaded to the same HINT `R##/rl/ubuntu/` folder and extracted there; immutable worker-v1 files are SHA-256 verified.
- The previous Gridworld, voice, gaze, Shimmer, HoloLens, workflow, and readable folder naming behavior is retained.

### Study 1(b) setup

1. On Ubuntu, start the previously prepared worker:

   ```bash
   cd HINT_ContinuousNav_Ubuntu
   bash scripts/run_worker.sh
   ```

2. On Ubuntu, find its LAN address with `hostname -I`. Keep TCP port `8875` reachable from the Windows study PC; port `8766` stays local to Ubuntu.
3. On Windows, install/update dependencies with `pip install -r requirements.txt`, start `python main.py`, open **Workflow → Study 1 — Study**, select **1(b). Continuous action-space room navigation**, enter the Ubuntu IP, then press **Connect / Test**.
4. Connect HoloLens, Shimmer, and the selected Keyboard or Joystick before starting the run.
5. When the run starts, the participant room window opens automatically. The participant watches the live agent; after collision/rewind, it switches to Human Control for the configured `N` steps.

### Study 1(b) synchronized files

For example:

```text
data/P001/S01/Study1_ExplicitFeedback/
  T03_Room_Requested_Keyboard/R01/
    sensors/
      hololens/...
      shimmer/...
    input/
      continuous_nav_actions.csv
    events/
      ubuntu_remote_events.jsonl
      ...
    rl/
      continuous_nav_console_config.json
      continuous_nav_state_stream.csv
      ubuntu/
        ubuntu_trial_bundle.zip
        extracted/
          manifest.json
          config_snapshot.json
          world_geometry.json
          worker_events.jsonl
          hil_events.jsonl
          rl_steps.csv
          ubuntu_stdout.log
          ubuntu_stderr.log
          checksums.json
```

## What's in this version (V1.1.13 — PV-camera gaze-axis fix)

### Eye-gaze tracker-axis correction (V1.1.13)
- Raw Extended Eye Tracking rays are no longer interpreted directly as screen X/Y.
- Each gaze ray is transformed from tracker space into the PV camera frame before LEFT/RIGHT/UP/DOWN classification.
- PV-camera semantics are: +X RIGHT, -X LEFT, -Y UP, +Y DOWN.
- Direction-debug CSV now records camera-frame gaze components and coordinate-frame name.
- If a PV transform is temporarily unavailable, that sample is ignored instead of being classified in the wrong frame.


- Fixes the real HoloLens EET forward-axis sign bug that caused positive-Z gaze rays to be rejected as `gaze_not_forward`.
- Keeps the v1.1.11 live gaze debug overlay and `gaze_direction_debug.csv` troubleshooting log.
- Windowed likelihood direction recognition now accepts both positive-Z and negative-Z tracker conventions by using the forward-axis magnitude.

- Eye Gaze direction feedback now logs every fresh troubleshooting sample as `GAZE_DIRECTION_DEBUG`.
- A dedicated `sensors/hololens/gaze_direction_debug.csv` is created lazily during Eye Gaze direction feedback, with raw gaze vector, yaw/pitch, local-center angles, relative angular offsets, instantaneous label, rolling direction/confidence/margin, per-class probabilities, sample counts, and invalid/stale reasons.
- The HoloLens camera/gaze preview now includes a live Direction Debug line showing `Δyaw`, `Δpitch`, instantaneous direction, rolling direction, confidence, margin, sample count, and LEFT/RIGHT/UP/DOWN/CENTER probabilities.
- Stale or invalid EET input is displayed explicitly instead of silently waiting.
- Requested-feedback instructions now tell the participant to look normally at the agent/maze first so the local gaze center is captured before looking toward the intended direction.
- The v1.1.10 windowed gaze-likelihood classifier itself is unchanged so the new diagnostics can reveal exactly which part of the recognition pipeline is failing.

## What's in this version (V1.1.10 — windowed gaze-likelihood direction feedback)

- Eye-gaze direction commands no longer require an uninterrupted 0.40-second hold.
- Direction is estimated from the **likelihood of recent valid gaze samples** inside a configurable time window.
- Missing/invalid EET direction samples are ignored during direction recognition instead of resetting the gesture.
- The default classifier uses a 0.70-second evidence window, requires at least 5 valid samples, accepts a direction at >=70% confidence, and requires a >=20% lead over the next-best class.
- LEFT/RIGHT/UP/DOWN/CENTER sample likelihoods are computed relative to the participant's local gaze center using angular Gaussian prototypes.
- The participant window shows the current best direction, confidence, and valid-sample count while evidence is being accumulated.
- The existing confirmation beep is retained and is played only after a direction passes the confidence checks and is accepted.
- Double-blink pause, one-second eye-close delimiter, N-blink state selection, HoloLens camera/gaze preview, and the HoloLens recording/path fixes remain unchanged.

## What's in this version (V1.1.7 — feedback-window camera + eye-gaze preview)

- Eye Gaze feedback interactions now show the already-running HoloLens PV/front-camera stream directly inside the participant Human Feedback panel.
- The live preview overlays the projected combined-eye gaze cursor, matching the validation/recording overlay.
- The preview activates only while Eye Gaze feedback is actively being given: on a system-requested feedback prompt, or after an Anytime pause has opened state selection.
- The preview remains active through Anytime state-number selection and corrective direction entry, then stops and hides when feedback resolves or the trial stops.
- Stream/calibration health is shown under the preview (eye calibration, gaze visibility, PV age, EET age).
- The preview reuses the existing HoloLens connection and does not create a second HL2SS camera or eye-tracking stream.

## What's in this version (V1.1.6 — Study 2 eye-gaze feedback)

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
  Gridworld feedback. Keyboard, **Voice, and Eye Gaze** Gridworld runs now
  launch the live Actor-Critic integration in both Study 2 Training and Study 2
  Study. Voice uses the microphone selected on the Devices page and local Vosk
  speech recognition. Eye Gaze uses the existing live HoloLens 2 Extended Eye
  Tracking stream. Joystick remains tracked until its dedicated feedback adapter
  is connected.

- **Event Log** — live event feed + disk usage, for monitoring/debugging.
  Device status and current-session context now live on the Devices and
  Workflow pages instead.

The participant-facing second window (maze view + feedback controls) opens
automatically for live Keyboard, Voice, and Eye Gaze Gridworld trials. In Voice
Requested Feedback, the participant says **UP / DOWN / LEFT / RIGHT**. In Voice
Anytime Feedback, the participant says **STOP**, then the number of one of the
displayed recent-state boxes, then **UP / DOWN / LEFT / RIGHT**.

In Eye Gaze Requested Feedback, the participant looks clearly in the desired
direction until the system has accumulated enough valid gaze evidence and plays
the confirmation beep. Missing EET direction samples do not reset the evidence.
In Eye Gaze Anytime Feedback, **two blinks** pause the agent; the participant then
closes both eyes for about one second, opens them, blinks **N** times to choose
state box N, and finally looks clearly in the corrective direction until the
beep confirms recognition. Direction likelihood is computed relative to the
participant's fixation when the feedback stage begins, rather than assuming the
display is perfectly aligned with the headset.

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
      pv_gaze.mp4
      gaze.csv
      eet.csv
      meta.json

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
├── remote/                    # Ubuntu worker communication + synchronized remote logs
│   └── continuous_nav_client.py
├── devices/                   # device interfaces + hardware adapters
│   ├── base_device.py          # BaseDevice interface, MockDevice
│   ├── shimmer_protocol.py     # Shimmer3 LiteProtocol helpers + packet parser
│   ├── shimmer_device.py       # real Bluetooth/serial GSR+PPG streaming
│   ├── input_devices.py        # keyboard/joystick/microphone adapters + PCM phrase capture
│   ├── voice_recognizer.py     # local Vosk STOP/number/direction recognition
│   ├── gaze_gesture_recognizer.py # HoloLens blink/count/windowed-gaze gestures
│   └── device_manager.py       # owns one device per DeviceType
├── gui/                       # PySide6 widgets
│   ├── main_window.py          # left nav (Devices / Workflow / Event Log)
│   ├── continuous_nav_window.py # Study 1(b) live room + lock-step feedback
│   ├── devices_page.py, device_status_strip.py
│   ├── workflow_page.py        # participant selector + step menu + detail panels
│   ├── registration_panel.py   # Registration step detail
│   ├── study1_step_panel.py    # Study 1 Training/Study (real RL trial)
│   ├── study2_step_panel.py    # Study 2 Training; live Keyboard/Voice/Eye Gaze
│   ├── participant_dialog.py   # "New Participant" dialog
│   ├── participant_window.py   # participant maze + multimodal feedback state machine
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
- The Ubuntu indoor-room adapter is now integrated for Study 1(b). The experimenter
  baseline remains a tracked task, and modality-specific limitations described in
  their individual panels still apply.
- `STUDY` mode config locking is read but not yet enforced in the UI.
- Session/task time-limit **enforcement** (vs. display) is not yet wired
  into the workflow steps.

## IRB-aligned study flow (v0.6)

- **Study 1 — Training:** existing 2 x 4 familiarization matrix remains unchanged.
- **Study 1 — Study:** 4 required protocol conditions across three settings: Gridworld Requested, Gridworld Anytime, Study 1(b) Continuous Room collision-triggered Requested feedback, and Experimenter Baseline. Experimental Study 1 uses Keyboard/Joystick only.
- **Study 2 — Training:** existing training/session-tracking window remains in place.
- **Study 2 — Study:** Gridworld-focused multimodal tracker with Keyboard, Joystick, Voice, and Implicit (Gaze/Physiological) conditions.

Live integrated RL paths now include the local Actor-Critic Gridworld and the Ubuntu Study 1(b) continuous-room worker. Remaining non-integrated modalities/environments use tracked Trials rather than being silently substituted. See `IRB_FLOW_ALIGNMENT_CHANGELOG.md` and `UBUNTU_STUDY1B_INTEGRATION_CHANGELOG.md`.


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
