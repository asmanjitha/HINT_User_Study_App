import numpy as np

class MazeEnv:
    def __init__(self):
        # Hard-coded 10x10 static maze
        # 0: Empty, 1: Wall
        self.maze = np.array([
            [0, 0, 0, 0, 0, 0, 1, 0, 0, 0],
            [0, 1, 1, 1, 1, 0, 1, 0, 1, 0],
            [0, 0, 0, 0, 1, 0, 0, 0, 1, 0],
            [0, 1, 1, 0, 1, 1, 1, 1, 1, 0],
            [0, 0, 1, 0, 0, 0, 0, 0, 0, 0],
            [1, 0, 1, 1, 1, 1, 1, 1, 1, 0],
            [0, 0, 0, 0, 0, 0, 0, 0, 1, 0],
            [0, 1, 1, 1, 1, 1, 1, 0, 1, 0],
            [0, 0, 0, 0, 0, 0, 1, 0, 0, 0],
            [0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
        ])
        self.size = 10
        self.max_steps = 100

        # Fixed positions
        self.start_pos = (0, 0)
        self.target_pos = (9, 9)

        self.reset_episode()

    def reset_episode(self):
        """Resets to fixed starting position."""
        self.steps = 0
        self.done = False
        self.agent_pos = list(self.start_pos)
        return self._get_state()

    def _get_state(self):
        return tuple(self.agent_pos)

    def step(self, action):
        """
        Actions: 0: Up, 1: Down, 2: Left, 3: Right
        Returns: next_state, reward, done, target_reached
        """
        self.steps += 1
        prev_pos = list(self.agent_pos)
        target_reached = False

        if action == 0:  # Up
            self.agent_pos[0] -= 1
        elif action == 1:  # Down
            self.agent_pos[0] += 1
        elif action == 2:  # Left
            self.agent_pos[1] -= 1
        elif action == 3:  # Right
            self.agent_pos[1] += 1

        # Check boundaries and collisions
        if (self.agent_pos[0] < 0 or self.agent_pos[0] >= self.size or
            self.agent_pos[1] < 0 or self.agent_pos[1] >= self.size or
            self.maze[tuple(self.agent_pos)] == 1):

            self.done = True
            reward = -50  # Collision penalty
            return self._get_state(), reward, self.done, target_reached

        # Check if target reached
        if tuple(self.agent_pos) == self.target_pos:
            target_reached = True
            self.done = True  # Episode ends when target reached
            reward = 100  # Success reward
            return self._get_state(), reward, self.done, target_reached

        # Check max steps
        if self.steps >= self.max_steps:
            self.done = True
            reward = -10  # Time limit penalty
            return self._get_state(), reward, self.done, target_reached

        # Time penalty and proximity
        reward = -1  # Base time penalty

        # Proximity reward
        prev_dist = abs(prev_pos[0] - self.target_pos[0]) + abs(prev_pos[1] - self.target_pos[1])
        curr_dist = abs(self.agent_pos[0] - self.target_pos[0]) + abs(self.agent_pos[1] - self.target_pos[1])
        if curr_dist < prev_dist:
            reward += 0.5
        else:
            reward -= 0.5

        return self._get_state(), reward, self.done, target_reached
