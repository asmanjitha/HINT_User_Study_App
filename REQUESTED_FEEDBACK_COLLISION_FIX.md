# V1.1.5 — Requested-feedback collision resume fix

## Problem
In System Requested feedback mode, the Actor-Critic Gridworld stops its regular step timer while waiting for human feedback. V1.1.4 immediately executes the participant-selected action. If that action ends the episode (especially by colliding with a wall/boundary), `_finish_episode()` resets the environment to the start state, but the stopped step timer was never restarted. The display therefore showed the new episode at the start cell while the agent remained frozen.

Because Keyboard and Voice share the same requested-feedback execution path, the issue affected both modalities and both training/study runs that use this experiment.

## Fix
`_finish_episode()` now restarts the normal RL step timer whenever the experiment is still running, is not administratively paused, and is not inside another feedback-selection state. This safely covers episode termination caused by requested human actions as well as ordinary episode completion.

## Regression test
Added a test that simulates requested feedback at `(0, 0)`, submits `UP` (an invalid/collision action), verifies the environment resets to `(0, 0)` for episode 2, and verifies the RL step timer is restarted.
