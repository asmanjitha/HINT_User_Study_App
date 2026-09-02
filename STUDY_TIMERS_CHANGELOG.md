# Study Timers — v1.3.1

## Added

- Researcher-console countdown shown in the main-window status bar only.
- 8-minute countdown for every experimental Study 1 condition.
- 8-minute countdown for every experimental Study 2 condition.
- 5-minute countdown for every Agent Observation condition.
- Countdown pause/resume synchronization for pausable Gridworld trials.
- Automatic valid completion at `00:00`, including synchronized trial, workflow,
  sensor, and Ubuntu-runner shutdown through the existing trial close path.
- `TRIAL_TIME_LIMIT_REACHED` audit event and workflow note for timed completions.

## Configuration

Durations can be adjusted in `config/study.yaml` under `timing`:

```yaml
study_1_condition_minutes: 8
study_2_condition_minutes: 8
observation_condition_minutes: 5
```

Training/familiarization trials are intentionally not timed.
