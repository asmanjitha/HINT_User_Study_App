# Shimmer Trial Recording — v0.8

## Goal
Keep the Shimmer connected and streaming continuously, but save the GSR/PPG data
that belongs to each experimental Study 1/Study 2 trial inside that trial's own
`sensors/` directory.

## Behavior
- Connecting Shimmer still creates a connection-level diagnostic CSV under
  `data/device_logs/shimmer/`.
- Starting an **experimental** Study 1 or Study 2 trial automatically opens:
  - `trials/<trial_id>/sensors/shimmer_gsr_ppg.csv`
  - `trials/<trial_id>/sensors/shimmer_recording_metadata.json`
- Incoming stream samples are duplicated into the trial CSV without stopping or
  reconfiguring the Shimmer Bluetooth stream.
- Stopping/completing/aborting the trial flushes and closes the trial CSV.
- Practice/training trials are intentionally excluded from primary physiological
  study recording, matching the protocol's separation of familiarization data.
- If no fresh Shimmer stream exists at study start, the Study 1/2 GUI warns the
  researcher. They may return to Devices to reconnect/verify or explicitly
  continue without physiological recording.

## CSV fields
The trial CSV stores condition metadata plus synchronized timing and raw data:
participant/session/trial IDs, study/environment/timing/modality, trial start,
host epoch/UTC/monotonic timestamps, trial elapsed time, global stream sample
index, trial sample index, Shimmer timestamp, GSR raw/ADC/range, and PPG raw.

## Verification in GUI
The Devices page now shows:
- connection diagnostic CSV
- whether a study recording is active
- active trial ID and sample count
- exact trial GSR/PPG CSV path
