"""Headless simulation: fast, complete, and internally consistent scores."""

import time

from bots.random_bot import RandomBot
from leakbots.config import DEFAULT_CONFIG
from leakbots.loader import load_bot
from leakbots.simulation import run_games, seat_labels


def test_hundred_games_fast_and_consistent():
    start = time.perf_counter()
    result = run_games([RandomBot], n_games=100, seed=0)
    assert time.perf_counter() - start < 15
    assert result.games == 100 and not result.cancelled
    seat = result.seats[0]
    c = DEFAULT_CONFIG
    for i in range(100):
        # Score must equal the breakdown under the configured point values
        expected = (
            seat.pipes_detected[i] * c.points_pipe_detected
            + seat.leaks_found[i] * c.points_leak_found
            + seat.leaks_fixed[i] * c.points_leak_fixed
            - seat.open_holes[i] * c.penalty_open_pipe_hole
        )
        assert seat.scores[i] == expected
    assert seat.wins == 100  # single player always "wins"


def test_wins_add_up_in_multiplayer():
    result = run_games([RandomBot, RandomBot, RandomBot], n_games=20, seed=5)
    assert abs(sum(s.wins for s in result.seats) - 20) < 1e-9
    assert [s.label for s in result.seats] == ["RandomBot #1", "RandomBot #2", "RandomBot #3"]


def test_stop_flag_cancels():
    result = run_games([RandomBot], n_games=50, should_stop=lambda: True)
    assert result.cancelled and result.games == 0


def test_loader_resolves_specs():
    assert load_bot("random_bot:RandomBot") is RandomBot
    assert load_bot("random_bot") is RandomBot
    assert seat_labels([RandomBot]) == ["RandomBot"]
