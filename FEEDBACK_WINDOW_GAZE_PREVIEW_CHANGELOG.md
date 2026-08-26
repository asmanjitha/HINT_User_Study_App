# V1.1.7 — Feedback-window HoloLens camera + eye-gaze preview

## Requested change

During Eye Gaze feedback, show the live HoloLens front/PV camera feed and the projected eye-gaze overlay in the participant feedback-giving window so the researcher/participant can immediately verify that the camera and eye tracking are working.

## Behavior

### System-requested feedback

When an Eye Gaze feedback request is emitted, the Human Feedback panel opens a live `HoloLens Camera + Eye Gaze` view. It stays active while the participant performs the direction-gaze recognition stage and closes after the feedback is resolved or times out.

### Anytime feedback

The preview is not shown during normal autonomous navigation. After the participant's double-blink pause command is accepted and the recent-state selection interface appears, the live preview opens. It stays visible while the participant performs the long eye-close delimiter, blink-count state selection, and final direction-gaze command. It closes when the correction is applied and navigation resumes.

## Stream handling

The preview calls `DeviceManager.hololens_latest_camera_gaze_snapshot()` and therefore reads the same already-running PV/EET streams used by recording and gesture recognition. No additional HL2SS receiver is opened.

The panel refreshes at 50 ms intervals while active and stops its timer when hidden. This avoids unnecessary GUI rendering during the rest of the trial.

## Display

- live PV/front RGB frame;
- cyan/white projected gaze cursor when the projected gaze ray is inside the PV image;
- eye calibration status;
- whether the gaze cursor is visible/outside/unavailable;
- age of the latest PV frame and EET sample.

The gaze point is a pose-registered projection at the same assumed 1.5 m distance used by the existing HoloLens validation view. It is intended as a live verification aid, not as an exact depth-aware fixation point.

## Validation

The existing automated suite continues to pass after this change.
