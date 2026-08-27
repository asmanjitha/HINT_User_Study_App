# HINT Console v1.2.1 — Study 1(b) startup/failure fixes

- Ubuntu worker port is editable directly in the Study 1(b) panel.
- Default worker port is 8875 to avoid Foxglove Bridge's common 8765 port.
- `RL_PROCESS_FAILED` from Ubuntu is surfaced as a visible error with the stderr tail.
- The participant room window switches to an explicit RL-failed state instead of appearing frozen.
- Existing Study 1(a), HoloLens, Shimmer, gaze, voice, and data-folder behavior is unchanged.

Use with HINT Ubuntu Worker v1.1 or newer for RL-Python preflight and immediate `TASK_ALREADY_STOPPED` acknowledgement.
