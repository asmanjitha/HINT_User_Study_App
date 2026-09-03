# Study 3 Observation Video Update — v1.8.0

## New Study 3 execution

- Replaced live Gridworld and Ubuntu room execution in Study 3 with two local
  prerecorded agent-training videos.
- Added persistent Gridworld and room-video selectors to the researcher panel.
- Added a dedicated participant video window with fullscreen playback.
- Preserved the participant-controlled **START ACTIVITY** gate.

## Synchronized recording lifecycle

- Participant Start begins the Trial, HoloLens PV/EET, Shimmer GSR/PPG, and
  video playback at the same boundary.
- Natural end-of-media stops/finalizes both sensor recordings and marks the
  observation Valid.
- Researcher Valid, Invalid/Repeat, and Abort controls remain available.
- Video playback errors are shown to the participant and recorded in the event
  log without silently accepting the run.

## Reproducibility and timing

- Each run stores `observation_video/source.json` with the selected media path,
  name, file size, modification time, fullscreen mode, and start-gate setting.
- Study 3 uses the video's natural duration and is no longer stopped by the old
  five-minute countdown.
- Training and Study 1 retain their existing live Continuous Room worker path.
