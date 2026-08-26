# Anytime Feedback: Pause → Select State → Correct

This version changes the Actor-Critic Gridworld **Anytime Feedback** workflow.

## Participant workflow

1. During normal Anytime training, direction keys/buttons do not directly submit feedback.
2. The participant presses **SPACE** or **PAUSE & SELECT FEEDBACK**.
3. The RL movement timer stops without administratively pausing the trial/session.
4. The participant window shows up to the last 10 visited states from the current episode.
   - States are numbered on the maze.
   - Buttons show episode step, cell, and how many steps back the state is.
5. The participant selects one state.
6. The selected state is previewed on the maze and the direction controls become enabled.
7. The participant chooses UP/DOWN/LEFT/RIGHT.
8. Human guidance is applied to the Actor-Critic policy for the selected historical state.
9. The live environment is **not rewound**; training resumes from the state where it was paused.

## Configuration

`config/study.yaml` now contains:

```yaml
anytime_history_length: 10
```

## Logging

`rl/interventions.csv` now also records:

- `selected_step`
- `pause_step`
- `steps_back`
- `pause_timestamp`

`events/events.csv` records an `ANYTIME_FEEDBACK_STARTED` event when the participant enters the state-selection flow.
