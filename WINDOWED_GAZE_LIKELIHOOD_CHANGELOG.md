# V1.1.10 — Windowed gaze-likelihood direction feedback

## Why this changed

The HoloLens EET stream can intermittently miss gaze-direction samples. The
v1.1.9 sustained-gaze mechanism required the same direction to remain valid for
an uninterrupted hold period, so a participant could look correctly but fail to
produce a command when packets were missing.

## New direction classifier

Direction recognition now uses a recent window of **valid** combined-gaze
samples. Missing/invalid gaze-direction samples are ignored; they neither count
as evidence nor reset the accumulated evidence.

1. The first valid gaze fixation when direction feedback begins becomes the
   local center.
2. Each subsequent valid combined-gaze ray is converted to horizontal/vertical
   angular displacement from that center.
3. A clear threshold-crossing sample begins a direction-evidence attempt.
4. Every valid sample in the recent window receives normalized likelihoods for
   LEFT, RIGHT, UP, DOWN, and CENTER using Gaussian angular prototypes.
5. The likelihoods are averaged across the valid samples in the window.
6. A direction is accepted only when there are enough valid samples, its
   probability exceeds the confidence threshold, and it leads the next-best
   class by the configured margin.
7. The existing confirmation beep plays and the direction is submitted through
   the normal Eye Gaze feedback path.

A weak/brief look followed by a strong return to center closes the tentative
evidence attempt. Evidence older than the configured window automatically
expires.

## Default configuration

```yaml
eye_gaze_recognition:
  direction_threshold_deg: 12.0
  direction_neutral_deg: 6.0
  direction_dominance_margin_deg: 2.0
  direction_window_seconds: 0.70
  direction_min_valid_samples: 5
  direction_probability_threshold: 0.70
  direction_probability_margin: 0.20
  center_adaptation_alpha: 0.05
```

`direction_threshold_deg` still controls how far the eyes must move before a
direction attempt begins. `direction_probability_threshold` controls confidence,
and `direction_probability_margin` controls how clearly the winning direction
must beat competing directions/center.

## Direction-stage handling of missing samples

During direction recognition, an all-invalid gaze packet is treated only as
missing data. It is **not** interpreted as a blink and does not cause the next
valid gaze sample to be discarded. Blink and long-eye-close interpretation are
unchanged in the explicit Anytime pause/state-selection contexts.

## Participant-window feedback

While accumulating evidence, the feedback panel can display progress such as:

```text
Gaze evidence: LEFT 82% (5/5 valid samples minimum).
Keep looking clearly in the intended direction until you hear the beep.
```

The live HoloLens camera + gaze overlay remains visible during Eye Gaze feedback.

## Validation

Regression tests cover:

- all four windowed gaze directions;
- missing packets between every valid directional sample;
- missing packets not counting toward the valid-sample minimum;
- rejecting a single brief direction glance followed by center;
- rejecting ambiguous diagonal gaze;
- expiring old evidence outside the configured window;
- emitting only one accepted direction command;
- all existing blink, voice, keyboard, RL reset, and HoloLens recording tests.
