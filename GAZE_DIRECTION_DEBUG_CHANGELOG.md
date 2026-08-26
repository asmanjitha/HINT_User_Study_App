# V1.1.11 — Gaze direction troubleshooting logs

This build adds detailed instrumentation around the v1.1.10 windowed gaze-likelihood classifier without changing its acceptance algorithm.

## Live debug view

During Eye Gaze direction feedback, the HoloLens Camera + Eye Gaze panel now shows:

- relative horizontal gaze angle (`Δyaw`)
- relative vertical gaze angle (`Δpitch`)
- instantaneous threshold-based direction (`LEFT`, `RIGHT`, `UP`, `DOWN`, `CENTER`, or `AMBIGUOUS`)
- rolling maximum-likelihood direction
- rolling confidence and confidence margin
- valid sample count / required sample count
- rolling LEFT/RIGHT/UP/DOWN/CENTER probabilities
- explicit STALE or INVALID stream states

## CSV logging

Every direction-debug event is still written to `events/events.csv` using the event type `GAZE_DIRECTION_DEBUG`. In addition, a dedicated CSV is created for easier troubleshooting:

`<trial>/sensors/hololens/gaze_direction_debug.csv`

The CSV records the raw combined gaze vector, absolute yaw/pitch, local center, relative yaw/pitch, instantaneous and rolling direction labels, likelihoods, valid sample counts, calibration/gaze-validity state, and stale/invalid reasons.

## Center-reference note

The direction classifier uses the first valid gaze sample after direction recognition starts as its local center reference. The participant instructions now explicitly ask the user to look normally at the agent/maze first, then move their eyes toward the desired direction. The debug CSV records the `center_set` row so center-reference errors can be diagnosed.

## Configuration

```yaml
eye_gaze_recognition:
  direction_debug_enabled: true
  direction_debug_stale_seconds: 0.25
```

Set `direction_debug_enabled: false` after calibration/troubleshooting if the per-sample diagnostic log is no longer needed.
