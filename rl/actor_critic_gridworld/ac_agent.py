"""
ac_agent.py  –  Tabular Softmax Actor-Critic (one-step TD)
===========================================================

Human feedback update:
- Actor:
    directly boost the human-approved action logit
    and reduce the other action logits
- Critic:
    increase the value estimate of the state

This is separate from the normal TD actor-critic update.
"""

import numpy as np
from pathlib import Path


class ActorCriticAgent:
    def __init__(
        self,
        state_size,
        action_size,
        actor_lr=0.02,
        critic_lr=0.10,
        discount_factor=0.99,
        entropy_coef=0.05,
        entropy_decay=0.9995,
        entropy_min=0.002,
        logit_clip=10.0,
        human_actor_boost=3.0,     # amount added to chosen action logit
        human_actor_reduce=1.0,    # amount subtracted from other action logits
        human_critic_bonus=5.0,     # amount added to V(s)
        random_seed=None
    ):
        self.action_size = action_size

        self.actor_lr = actor_lr
        self.critic_lr = critic_lr
        self.gamma = discount_factor
        self.entropy_coef = entropy_coef
        self.entropy_decay = entropy_decay
        self.entropy_min = entropy_min
        self.logit_clip = logit_clip

        self.human_actor_boost = human_actor_boost
        self.human_actor_reduce = human_actor_reduce
        self.human_critic_bonus = human_critic_bonus
        self.rng = np.random.default_rng(random_seed)

        # Actor: state -> logits
        self.actor_params = {}

        # Critic: state -> value
        self.critic_values = {}

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _get_logits(self, state) -> np.ndarray:
        if state not in self.actor_params:
            self.actor_params[state] = np.zeros(self.action_size, dtype=np.float64)
        return self.actor_params[state]

    def _get_value(self, state) -> float:
        return self.critic_values.get(state, 0.0)

    def _softmax(self, logits: np.ndarray) -> np.ndarray:
        shifted = logits - logits.max()
        exp_l = np.exp(shifted)
        return exp_l / exp_l.sum()

    def _policy(self, state) -> np.ndarray:
        return self._softmax(self._get_logits(state))

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def choose_action(self, state) -> int:
        probs = self._policy(state)
        return int(self.rng.choice(self.action_size, p=probs))

    def learn(self, state, action, reward, next_state, done):
        """
        Standard one-step actor-critic TD update.
        """
        # ---- Critic ----
        v_s = self._get_value(state)
        v_next = 0.0 if done else self._get_value(next_state)

        delta = reward + self.gamma * v_next - v_s
        self.critic_values[state] = v_s + self.critic_lr * delta

        # ---- Actor ----
        pi = self._policy(state)

        one_hot = np.zeros(self.action_size, dtype=np.float64)
        one_hot[action] = 1.0
        pg_grad = delta * (one_hot - pi)

        log_pi = np.log(pi + 1e-10)
        entropy = -np.sum(pi * log_pi)
        entropy_grad = -pi * (log_pi + entropy)

        logits = self._get_logits(state)
        logits += self.actor_lr * (pg_grad + self.entropy_coef * entropy_grad)
        np.clip(logits, -self.logit_clip, self.logit_clip, out=logits)

        if done:
            self.entropy_coef = max(
                self.entropy_min,
                self.entropy_coef * self.entropy_decay
            )

    # ------------------------------------------------------------------ #
    # Human feedback update: update BOTH actor and critic
    # ------------------------------------------------------------------ #

    def apply_human_guidance(self, state, chosen_action):
        """
        Update both Actor and Critic after human feedback.

        Actor:
            - strongly increase the chosen action preference
            - reduce the other action preferences

        Critic:
            - increase the state's value estimate

        This is NOT treated as an extra advantage signal.
        It is a direct correction step applied after human feedback.
        """
        logits = self._get_logits(state)

        for a in range(self.action_size):
            if a == chosen_action:
                logits[a] += self.human_actor_boost
            else:
                logits[a] -= self.human_actor_reduce

        np.clip(logits, -self.logit_clip, self.logit_clip, out=logits)

        self.critic_values[state] = self._get_value(state) + self.human_critic_bonus

    # ------------------------------------------------------------------ #
    # Maze-informed warm-start
    # ------------------------------------------------------------------ #

    def initialize_q_table_from_maze(self, maze, goal_pos):
        rows, cols = maze.shape
        goal_r, goal_c = goal_pos

        action_deltas = {
            0: (-1, 0),   # UP
            1: (1, 0),    # DOWN
            2: (0, -1),   # LEFT
            3: (0, 1),    # RIGHT
        }

        for r in range(rows):
            for c in range(cols):
                if maze[r, c] == 1:
                    continue

                state = (r, c, goal_r, goal_c)
                dist = abs(r - goal_r) + abs(c - goal_c)

                self.critic_values[state] = -float(dist)

                logits = np.zeros(self.action_size, dtype=np.float64)
                for a, (dr, dc) in action_deltas.items():
                    nr, nc = r + dr, c + dc

                    if nr < 0 or nr >= rows or nc < 0 or nc >= cols or maze[nr, nc] == 1:
                        logits[a] = -4.0
                    else:
                        new_dist = abs(nr - goal_r) + abs(nc - goal_c)
                        if new_dist < dist:
                            logits[a] = 2.0
                        elif new_dist > dist:
                            logits[a] = -1.0

                self.actor_params[state] = logits

        print("[AC-init] Maze-informed warm-start applied to Actor & Critic tables.")

    # ------------------------------------------------------------------ #
    # Snapshot / persistence
    # ------------------------------------------------------------------ #

    def save_q_table(self, filepath):
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)

        all_states = sorted(set(self.actor_params.keys()) | set(self.critic_values.keys()))

        with path.open("w", encoding="utf-8") as f:
            f.write("# Actor-Critic snapshot\n")
            f.write("[ACTOR]  state  action  logit  probability\n")
            f.write("state\taction\tlogit\tprobability\n")

            for s in all_states:
                if s in self.actor_params:
                    pi = self._softmax(self.actor_params[s])
                    for a in range(self.action_size):
                        f.write(
                            f"{s}\t{a}\t"
                            f"{self.actor_params[s][a]:.6f}\t"
                            f"{pi[a]:.6f}\n"
                        )

            f.write("\n[CRITIC]  state  value\n")
            f.write("state\tvalue\n")
            for s in all_states:
                f.write(f"{s}\t{self._get_value(s):.6f}\n")