# HINT Study Console v1.1.13 — Eye-gaze PV camera axis fix

## Problem
In v1.1.12, the direction recognizer converted raw Extended Eye Tracking tracker-space X/Y components directly into horizontal and vertical angles. On the real HoloLens this caused RIGHT to be detected as DOWN and DOWN to be detected as RIGHT.

Extended Eye Tracking gaze rays are expressed in the eye tracker's own coordinate system. The tracker axes are not guaranteed to align with the PV camera/display axes.

## Fix
Direction recognition now transforms every valid combined EET gaze ray:

1. tracker coordinates -> world using the EET pose;
2. world -> PV reference coordinates using the inverse PV pose;
3. PV reference -> PV camera convention (X right, Y down, Z forward).

Only after that transform are horizontal and vertical gaze angles calculated. In the classifier:

- positive camera-X -> RIGHT;
- negative camera-X -> LEFT;
- negative camera-Y -> UP;
- positive camera-Y -> DOWN.

The local-center and windowed-likelihood mechanisms are otherwise unchanged.

If the PV-camera transform is temporarily unavailable, the production recognizer now rejects that sample as missing data rather than falling back to raw tracker axes.

## Debugging
`gaze_direction_debug.csv` now also records:

- `coordinate_frame`
- `camera_x`
- `camera_y`
- `camera_z`

For real HoloLens direction samples, `coordinate_frame` should be `pv_camera`.

## Regression tests
Tests cover all four PV-camera directions and verify that a rotated tracker coordinate frame is transformed before direction classification.
