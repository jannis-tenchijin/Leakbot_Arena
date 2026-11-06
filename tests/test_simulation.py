"""Headless simulation: fast, complete, and internally consistent scores."""

import time

from bots.random_bot import RandomBot
from leakbots.config import DEFAULT_CONFIG
from leakbots.loader import load_bot
from leakbots.simulation import SimulationRunner, run_rounds, seat_labels


def test_hundred_rounds_fast_and_consistent():
    start = time.perf_counter()
    result = run_rounds([RandomBot], n_rounds=100, seed=0)
    assert time.perf_counter() - start < 15
    assert result.rounds == 100 and not result.cancelled
    seat = result.seats[0]
    c = DEFAULT_CONFIG
    for i in range(100):
        # Score must equal the breakdown under the configured point values
        expected = (
            seat.pipes_detected[i] * c.points_pipe_detected
            + seat.leaks_found[i] * c.points_leak_found
            + seat.leaks_fixed[i] * c.points_leak_fixed
            - seat.open_holes[i] * c.penalty_open_pipe_hole
            - seat.spent[i]
            + seat.bonus[i]
            + seat.valves_reopened[i] * c.points_valve_reopened
            - seat.closed_valves[i] * c.penalty_closed_valve
        )
        assert seat.scores[i] == expected
    assert seat.wins == 100  # single player always "wins"


def test_wins_add_up_in_multiplayer():
    result = run_rounds([RandomBot, RandomBot, RandomBot], n_rounds=20, seed=5)
    assert abs(sum(s.wins for s in result.seats) - 20) < 1e-9
    assert [s.label for s in result.seats] == ["RandomBot #1", "RandomBot #2", "RandomBot #3"]


def test_stop_flag_cancels():
    result = run_rounds([RandomBot], n_rounds=50, should_stop=lambda: True)
    assert result.cancelled and result.rounds == 0


def test_runner_matches_run_rounds_and_respects_budget():
    runner = SimulationRunner([RandomBot], n_rounds=12, seed=9)
    runner.advance(0.0)  # zero budget still plays exactly one round
    assert runner.played == 1
    while not runner.done:
        runner.advance(0.01)
    direct = run_rounds([RandomBot], n_rounds=12, seed=9)
    assert runner.result().seats[0].scores == direct.seats[0].scores


def test_loader_resolves_specs():
    assert load_bot("random_bot:RandomBot") is RandomBot
    assert load_bot("random_bot") is RandomBot
    assert seat_labels([RandomBot]) == ["RandomBot"]
