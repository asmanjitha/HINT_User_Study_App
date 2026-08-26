# HINT Study Console v1.1.12 — HoloLens positive-Z gaze fix

## Problem
Real HoloLens EET logs showed valid forward-looking gaze rays with a positive Z component (for example `z=0.99`). v1.1.11 assumed tracker forward was negative Z and computed `forward = -z`, causing those samples to be rejected as `gaze_not_forward` before center calibration or direction classification.

## Fix
Direction recognition now uses the magnitude of the tracker forward component (`forward = abs(z)`) when converting the normalized gaze ray to horizontal/vertical angles. Because the classifier is relative to the locally established center fixation, the sign convention of tracker Z is irrelevant to the direction offset.

This supports the positive-Z vectors observed on the real HoloLens and remains compatible with legacy/synthetic negative-Z samples. Samples are rejected only when the forward-axis magnitude is effectively zero.

## Regression coverage
Added tests that use positive-Z center and RIGHT gaze rays and verify that:
- the first sample establishes the center;
- samples are not rejected as `gaze_not_forward`;
- a positive horizontal offset is classified as RIGHT;
- the rolling likelihood recognizer emits a RIGHT command after the configured number of valid samples.
