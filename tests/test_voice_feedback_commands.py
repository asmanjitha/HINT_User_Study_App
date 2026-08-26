from devices.voice_recognizer import (
    VOICE_CONTEXT_DIRECTION,
    VOICE_CONTEXT_STATE_NUMBER,
    VOICE_CONTEXT_STOP,
    parse_voice_command,
)


def test_stop_command_requires_stop_word() -> None:
    assert parse_voice_command(VOICE_CONTEXT_STOP, "stop") == "stop"
    assert parse_voice_command(VOICE_CONTEXT_STOP, "please stop the agent") == "stop"
    assert parse_voice_command(VOICE_CONTEXT_STOP, "go left") is None


def test_direction_commands_accept_short_phrases() -> None:
    assert parse_voice_command(VOICE_CONTEXT_DIRECTION, "up") == "up"
    assert parse_voice_command(VOICE_CONTEXT_DIRECTION, "move down") == "down"
    assert parse_voice_command(VOICE_CONTEXT_DIRECTION, "go to the left") == "left"
    assert parse_voice_command(VOICE_CONTEXT_DIRECTION, "right please") == "right"
    assert parse_voice_command(VOICE_CONTEXT_DIRECTION, "box five") is None


def test_state_number_commands_accept_words_and_digits() -> None:
    assert parse_voice_command(VOICE_CONTEXT_STATE_NUMBER, "one") == "1"
    assert parse_voice_command(VOICE_CONTEXT_STATE_NUMBER, "box five") == "5"
    assert parse_voice_command(VOICE_CONTEXT_STATE_NUMBER, "state 7") == "7"
    assert parse_voice_command(VOICE_CONTEXT_STATE_NUMBER, "number ten") == "10"
    assert parse_voice_command(VOICE_CONTEXT_STATE_NUMBER, "eleven") is None


def test_requested_voice_feedback_executes_the_spoken_direction(tmp_path) -> None:
    import time

    from models.enums import Environment, FeedbackTiming, Modality, Study
    from models.trial import ExperimentCondition, Trial
    from rl.actor_critic_gridworld.experiment import ActorCriticGridworldExperiment

    trial_dir = tmp_path / "trial"
    (trial_dir / "rl").mkdir(parents=True)
    condition = ExperimentCondition(
        study=Study.STUDY_2,
        environment=Environment.GRIDWORLD,
        feedback_timing=FeedbackTiming.REQUESTED,
        modality=Modality.VOICE,
        random_seed=42,
    )
    trial = Trial(
        trial_id="voice_requested",
        session_id="session",
        participant_code="P001",
        condition=condition,
        trial_dir=str(trial_dir),
    )
    experiment = ActorCriticGridworldExperiment(
        trial=trial,
        config={"step_interval_ms": 1000, "feedback_timeout_seconds": 10},
    )

    # Simulate a system request at the live start state. DOWN from (0, 0)
    # is a valid transition to (1, 0) in the fixed maze.
    experiment._running = True
    experiment._waiting_for_feedback = True
    experiment._pending_ambiguity = (0, 0)
    experiment._pending_request_timestamp = time.time()

    assert experiment.submit_human_feedback(1, Modality.VOICE) is True
    assert experiment.state == (1, 0)
    assert experiment.env.steps == 1
    experiment.stop()


def test_requested_feedback_collision_resets_and_restarts_agent(tmp_path) -> None:
    import time

    from models.enums import Environment, FeedbackTiming, Modality, Study
    from models.trial import ExperimentCondition, Trial
    from rl.actor_critic_gridworld.experiment import ActorCriticGridworldExperiment

    # Training/study panels and Keyboard/Voice all route through this same
    # experiment path. Exercise both study identities and both modalities so
    # the shared regression cannot silently reappear in one condition.
    cases = [
        (Study.STUDY_1, Modality.KEYBOARD),
        (Study.STUDY_1, Modality.VOICE),
        (Study.STUDY_2, Modality.KEYBOARD),
        (Study.STUDY_2, Modality.VOICE),
    ]

    for case_index, (study, modality) in enumerate(cases):
        trial_dir = tmp_path / f"trial_collision_{case_index}"
        (trial_dir / "rl").mkdir(parents=True)
        condition = ExperimentCondition(
            study=study,
            environment=Environment.GRIDWORLD,
            feedback_timing=FeedbackTiming.REQUESTED,
            modality=modality,
            random_seed=42,
        )
        trial = Trial(
            trial_id=f"requested_collision_{case_index}",
            session_id="session",
            participant_code="P001",
            condition=condition,
            trial_dir=str(trial_dir),
        )
        experiment = ActorCriticGridworldExperiment(
            trial=trial,
            config={"step_interval_ms": 1000, "feedback_timeout_seconds": 10},
        )

        # Reproduce the reported bug: requested feedback is active at (0, 0),
        # and UP is an invalid move that ends the episode with a wall/boundary
        # collision. Requested feedback has already stopped the normal timer.
        experiment._running = True
        experiment._waiting_for_feedback = True
        experiment._pending_ambiguity = (0, 0)
        experiment._pending_request_timestamp = time.time()

        starts = []
        experiment._step_timer.start = lambda *args: starts.append(args)

        assert experiment.submit_human_feedback(0, modality) is True

        # The collision ends episode 1 and resets to episode 2 at the maze start.
        assert experiment.current_episode == 2
        assert experiment.state == (0, 0)
        assert experiment.env.steps == 0
        assert experiment._waiting_for_feedback is False

        # Regression check: the stopped RL timer must be restarted after reset,
        # otherwise the participant sees the agent frozen at the start position.
        assert starts == [(experiment.step_interval_ms,)]
        experiment.stop()

