# HINT Study Console v1.4.0

## External-drive storage

- Added startup data-location selection with folder browsing.
- Added persistent `config/storage_location.yaml` settings.
- Added writable-folder and free-space checks.
- Added missing/disconnected-drive detection at startup.
- Redirects study folders, both SQLite databases, and logs to a custom root.
- Added a main-window Data button for changing the next-launch location.

## Flexible workflow

- Removed Training → Study 1, Study 1 → Study 2, and Study 2 → Observation
  start prerequisites.
- Study 2 can be explicitly finished without collecting a modality.
- Added whole-phase and selected-condition manual completion controls.
- Manual completions remain distinguishable from valid collected trials.
- Added `completion_overrides` database table and
  `WORKFLOW_COMPLETION_OVERRIDDEN` events.
- Manual completion never creates fake trial or sensor data.
