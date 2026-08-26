# V1.1.9 — Sustained eye-gaze direction feedback

## Change

Eye-gaze direction feedback no longer requires two repeated looks with a return
to center. In both system-requested and Anytime feedback, the participant now
looks sharply in the intended direction and holds that gaze until it is accepted.

The recognizer uses the existing local gaze-center baseline and configurable
angular thresholds. A direction must remain dominant beyond
`direction_threshold_deg` for `direction_hold_seconds` (default 0.40 s). Brief
glances are ignored. Once a direction is accepted, it is latched so a continued
stare cannot generate repeated commands.

When UP/DOWN/LEFT/RIGHT is accepted, the participant window plays a short
1200-Hz confirmation tone on Windows (with a Qt application-beep fallback), then
submits the eye-gaze correction through the existing RL feedback path.

## Anytime protocol retained

Double blink -> pause -> ~1 s eye close -> blink N times for state N -> sustained
sharp gaze in corrective direction -> confirmation beep -> feedback applied.
