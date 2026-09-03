# Beam Eye Tracker Integration — v1.6.0

## Recording policy

- Required Training plus Studies 1 and 2 record webcam-based screen gaze with Beam.
- Optional HoloLens familiarization and the Agent Observation phase record HoloLens PV + EET.
- Shimmer behavior is unchanged: physiological data is recorded for experimental runs only.
- Sensor recording begins only when the participant presses Start and ends with the trial.

## Beam device workflow

- Added a first-class Beam device to the Devices page and persistent device-status strip.
- The experimenter selects the participant display before connecting.
- Live gaze coordinates, confidence, SDK/reception status, sample count, and active-recording state are shown.
- `Validate Live Gaze` checks for fresh tracking data before collection.
- The adapter requests Beam to launch if necessary. Webcam selection, positioning, and calibration remain in the Beam desktop application.

## Data written per run

`sensors/beam/gaze.csv` includes synchronized console UTC and monotonic timestamps,
trial elapsed time, Beam timestamp, bounded/unbounded screen gaze, normalized viewport
gaze, confidence, raw head rotation, derived Euler angles, translation, tracking-session
ID, and a validity flag.

`sensors/beam/recording_metadata.json` documents the viewport, coordinate frames,
SDK version, counts, trial identity, and stop reason.

HINT never opens Beam's webcam through OpenCV and does not record participant webcam video.

## Required software

- Beam Eye Tracker desktop application 2.6.3 or newer
- `beam-eye-tracker==2.2.0` Python package
