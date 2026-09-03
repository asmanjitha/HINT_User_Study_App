# Beam Participant-Display Capture Fix — v1.8.2

## Problem

Beam gaze-overlay video could capture the wrong monitor on a multi-display study
PC. The previous recorder used the display geometry selected when Beam was
connected and did not re-check where the participant-facing activity window was
actually running when a trial began.

## Fix

- Added **Automatic — follow participant activity window** as the recommended
  Beam screen-recording target.
- Immediately before participant Start begins recording, HINT resolves the
  native Windows monitor containing the Gridworld or Continuous Navigation user
  window.
- The Windows monitor is matched to MSS physical-pixel display geometry, avoiding
  Qt logical-pixel / Windows display-scaling mismatches.
- Beam's viewport is updated before `gaze.csv` and `screen_gaze.mp4` begin so the
  normalized gaze overlay and captured display use the same geometry.
- Added **Manual — Display N** targets plus **Apply Screen Target** on the Devices
  page for experimenter-controlled locking/fallback.
- Each trial snapshots its capture viewport so later UI/display changes cannot
  alter the geometry of an already-running MP4.
- Recording metadata now stores capture target mode, source, resolved viewport,
  fallback viewport, and the last target-resolution message.

## Behavior

In automatic mode, the participant may move the activity window to either
monitor before pressing **START ACTIVITY**. The recorder resolves the monitor at
that click and captures that display for the full run. If automatic resolution
fails, HINT keeps the experimenter-selected fallback display and logs the
fallback instead of blocking data collection.

## Verification

The Beam regression tests cover physical-display matching, automatic switching,
manual display locking, metadata capture geometry, gaze overlay mapping, and MP4
writing. The complete test suite passes.
