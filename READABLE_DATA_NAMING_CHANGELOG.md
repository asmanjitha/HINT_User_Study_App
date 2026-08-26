# V0.9 — Readable Data Naming + Repeat-Run Tracking

## Folder convention

Experimental collections now use:

```text
P###/S##/Study1_ExplicitFeedback/T##_Environment_Timing_Modality/R##/
P###/S##/Study2_MultimodalFeedback/T##_Environment_Timing_Modality/R##/
```

Training/familiarization uses:

```text
P###/S##/Training/Study1/TR##_Environment_Timing_Modality/R##/
P###/S##/Training/Study2/TR##_Environment_Timing_Modality/R##/
```

The internal session ID remains `P001_S01`, but its readable disk folder is
`P001/S01`.

## Stable condition + attempt semantics

- `T##` / `TR##` identifies an exact condition (study, environment, feedback
  timing, and modality).
- `R##` identifies one concrete collection attempt.
- Repeating the exact same condition reuses its condition folder and increments
  only the run: `R01 -> R02 -> R03`.
- A different condition receives the next available `T##` / `TR##`.
- Study 1, Study 2, Study 1 Training, and Study 2 Training each have their own
  condition-number namespace.

## Run validity

Experimental Study 1/2 panels now provide:

- **Stop & Mark Valid**
- **Mark Invalid / Repeat**
- **Abort Run**

Invalid and aborted attempts remain on disk and in SQLite. They do not count as
completed conditions. Invalid runs store a repeat reason such as participant
mistake, equipment failure, Shimmer disconnect, input-device problem,
experimenter error, or software error.

## GUI changes

- Study 1 and Study 2 show the next readable data folder before collection.
- During collection, the exact current data folder is displayed.
- Run History shows Condition (`T##`), Attempt (`R##`), condition information,
  and Valid/Invalid/Aborted result.

## Shimmer / RL metadata

Trial-local Shimmer CSVs now include:

- `condition_code`
- `run_code`
- `condition_name`

The same fields are also included in Shimmer recording metadata and the main RL
CSV common identifiers where applicable.

## Compatibility

Existing v0.8 SQLite databases are migrated in place with the new trial naming
columns. Existing data folders are not renamed retroactively; the new convention
is used for newly created collections.
