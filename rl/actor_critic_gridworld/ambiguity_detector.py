"""Oscillation-based ambiguity detector."""

from __future__ import annotations

from collections import deque


class OscillationAmbiguityDetector:
    def __init__(
        self,
        history_size: int = 20,
        cooldown_steps: int = 6,
    ) -> None:
        self.position_history = deque(maxlen=history_size)
        self.last_detection_step = -99999
        self.cooldown_steps = cooldown_steps

    def reset(self, initial_state: tuple[int, int]) -> None:
        self.position_history.clear()
        self.position_history.append(tuple(initial_state))
        self.last_detection_step = -99999

    def add_position(self, position: tuple[int, int]) -> None:
        self.position_history.append(tuple(position))

    def detect(self, current_step: int):
        if (
            current_step - self.last_detection_step
            < self.cooldown_steps
        ):
            return None

        hist = list(self.position_history)

        # Pattern 1:
        # A -> B -> A -> B -> A -> B
        if len(hist) >= 6:
            p = hist[-6:]

            if (
                p[0] == p[2] == p[4]
                and p[1] == p[3] == p[5]
                and p[0] != p[1]
            ):
                return p[-1]

        # Pattern 2:
        # A -> B -> C -> B -> A -> B -> C
        if len(hist) >= 7:
            p = hist[-7:]

            if (
                p[0] == p[4]
                and p[1] == p[3] == p[5]
                and p[2] == p[6]
                and p[0] != p[1]
                and p[1] != p[2]
                and p[0] != p[2]
            ):
                return p[-1]

        return None

    def mark_detection(self, current_step: int) -> None:
        self.last_detection_step = current_step