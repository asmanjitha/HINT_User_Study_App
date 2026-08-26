# HoloLens Study Recording — v1.1.3

## Added

Every persisted Training or Study trial now records HoloLens data when live PV + EET streams are available. The existing readable participant/session/condition/run structure is unchanged.

For each run, files are saved under:

```text
P001/S01/
  Training/Study1/TR01_.../R01/sensors/hololens/
  Study1_ExplicitFeedback/T01_.../R01/sensors/hololens/
```

The `sensors/hololens/` directory contains:

- `hololens_pv_gaze_overlay.mp4` — PV camera feed with the cyan gaze circle/white crosshair burned into the video.
- `hololens_gaze_pointer.csv` — one synchronized row per recorded PV frame, including raw projected pixel, smoothed/drawn overlay pixel, PV/EET timestamps, timestamp delta, validity flags, combined gaze ray, and trial timing fields.
- `hololens_eet_raw.csv` — every EET packet during the run, including combined/left/right gaze rays and validity/calibration flags.
- `hololens_recording_metadata.json` — condition identity, file names, configured rates, frame/sample counts, projection distance, and stop reason.

## Lifecycle

- HoloLens recording starts after the Trial starts.
- Study 2 Training now creates a normal practice Trial from its selected environment/timing/modality so it also receives a `Training/Study2/TR##.../R##/sensors/hololens/` directory.
- Recording is enabled for both `practice=True` training trials and primary study trials.
- Repeated conditions remain isolated by existing `R01`, `R02`, ... run folders.
- Valid, invalid, aborted, and trial-start-failed runs retain their own files; nothing is overwritten.
- HoloLens disconnection finalizes any open recording safely.
- If fresh PV+EET is unavailable when a run starts, the run is allowed to continue and an experimenter-note event records the missing HoloLens recording.

## Projection semantics

The overlay uses the existing pose-registered combined EET gaze projection at 1.5 m. It visualizes gaze direction; it is not a depth-resolved physical surface intersection.
