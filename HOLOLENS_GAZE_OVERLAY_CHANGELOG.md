# HoloLens PV gaze-overlay update

The HoloLens connection validation window now draws a moving combined-eye-gaze marker over the live PV/front-camera image.

## Registration method

- PV continues to use `StreamMode.MODE_1`, so each RGB frame includes a camera pose.
- EET packets now retain the packet pose in addition to the combined/left/right gaze rays.
- The device keeps a short EET history and selects the eye packet nearest in timestamp to the currently displayed PV frame.
- The combined EET gaze ray is transformed from eye-tracker coordinates to world coordinates using the EET pose, then from world coordinates into the PV camera using the PV pose.
- The point is projected with that PV frame's live focal length and principal point.
- The validation window draws a cyan circle/crosshair and applies light display-only smoothing.

## Important interpretation

HoloLens 2 Extended Eye Tracking does not provide a supported vergence/fixation distance. The validation marker therefore uses a point **1.5 m along the gaze ray**, matching the distance used by Microsoft's Extended Eye Tracking visualization sample.

This makes the marker useful for checking calibration, stream alignment, and gaze direction. It is **not** an exact ray/surface intersection. Exact physical fixation on near/far scene objects would require a depth map or spatial-mesh raycast.
