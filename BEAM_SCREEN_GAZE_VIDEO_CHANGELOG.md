# Beam Screen + Gaze Pointer Video — v1.7.0

## New output

All Beam-assigned activities now create:

```text
sensors/beam/
  gaze.csv
  screen_gaze.mp4
  recording_metadata.json
```

The video captures the selected participant display and draws Beam's normalized
viewport gaze over the screen. It begins at the participant-controlled Start
boundary and is finalized when the activity ends, is aborted, or the device is
disconnected.

## Pointer behavior

- High confidence: green
- Medium confidence: yellow
- Low confidence: orange
- Lost, outside-display, or stale gaze: pointer hidden

The upper-left status overlay includes trial elapsed time and the current gaze
state. Raw gaze remains available in `gaze.csv` for precise analysis.

## Multi-monitor and scaling behavior

The Devices page enumerates displays through MSS physical-pixel coordinates.
The selected geometry is used both as Beam's viewport and as the MP4 capture
rectangle, avoiding Qt logical-pixel offsets on Windows display scaling.

## Configuration

Edit `beam_screen_recording` in `config/study.yaml` to change the target FPS,
four-character OpenCV codec, pointer radius, stale-sample threshold, or status
overlay. `mss>=10.0` is included in `requirements.txt`.

The webcam is still owned exclusively by Beam and is never recorded by HINT.
