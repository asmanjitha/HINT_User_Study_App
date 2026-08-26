# v1.1.1 HoloLens 2 hotfix

- Removed the 3-second socket timeout from the Extended Eye Tracking (EET) receiver.
- Matches the official HL2SS EET sample behavior, which uses the default blocking socket.
- Prevents false `Extended Eye Tracking stream error: timed out` failures while HoloLens initializes EET or presents/handles eye-tracking permission/calibration.
- PV camera behavior is unchanged.

# HoloLens 2 Connection Integration

## What changed

The previous HoloLens `MockDevice` placeholder has been replaced by a real
network adapter based on the upstream **HL2SS** Python client.

The HINT Study Console now keeps two HoloLens 2 streams open after connection:

- Personal Video (PV/front RGB camera), default `1280 x 720 @ 30 FPS`.
- Extended Eye Tracking (EET), selectable at `30`, `60`, or `90 Hz`.

## Devices page workflow

A new **Microsoft HoloLens 2 — Eye Gaze + PV Camera** panel provides:

- detailed headset/PC/HL2SS setup instructions;
- HoloLens IPv4 address entry;
- browse/select control for the downloaded HL2SS repository root or `viewer` folder;
- Extended Eye Tracking rate selection;
- PV camera validation resolution selection;
- guided connection progress and diagnostic log;
- live camera/eye packet counts and packet ages;
- explicit eye-calibration validity status;
- **Validate Connection** button;
- **Disconnect** button;
- convenience buttons for the HL2SS repository and HoloLens Device Portal.

Connection settings (IP, HL2SS folder, eye rate) are remembered locally using
Qt `QSettings` for the next console launch.

## Validation-window behavior

After both the PV camera and Extended Eye Tracking streams have produced live
packets, the console changes the HoloLens state to `Receiving Data` and opens a
separate validation window **one time for that successful connection**.

The validation window contains:

- live HoloLens PV/front-camera feed;
- combined eye-gaze ray validity, origin, and direction;
- left-eye ray validity, origin, and direction;
- right-eye ray validity, origin, and direction;
- HoloLens eye-calibration validity;
- stream packet/frame counters and freshness checks.

Closing the validation window does **not** disconnect HoloLens or stop the
streams. Clicking **Validate Connection** later checks that new camera and eye
packets are still arriving, then reopens the window.

The console deliberately does not draw a gaze point directly on the PV image in
this milestone. Eye rays and PV frames have their own timestamped coordinate
systems; a scientifically correct 2-D overlay needs synchronization and spatial
registration rather than an arbitrary screen-coordinate mapping.

## HL2SS dependency model

The HINT repository does not vendor a copy of HL2SS. Download the official
HL2SS repository and select its repository root or `viewer` folder in the GUI.
The selected folder must contain at least `hl2ss.py` and `hl2ss_lnm.py`.

Install the updated Python requirements before using HoloLens:

```bash
pip install -r requirements.txt
```

The HoloLens must also have the matching/up-to-date HL2SS server app installed
and running.

## Research Mode

The current console integration uses PV camera + Extended Eye Tracking. Research
Mode is not required for those two streams in current HL2SS releases. Research
Mode should be enabled later only if HINT adds raw Research Mode VLC, depth, or
IMU streams.
