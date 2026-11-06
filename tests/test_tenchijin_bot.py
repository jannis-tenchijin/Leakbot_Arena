"""TenchijinBot: buys the satellite, digs D/E squares, fixes leaks, leaves no holes open.

The statistics run 30 seeded single-player rounds once (module fixture); the scenario tests
use a hand-made 25x25 map with a free satellite so the target order is known.
"""

from dataclasses import replace

import pytest

from bots.random_bot import RandomBot
from bots.tenchijin_bot import TenchijinBot, dig_payoff, leak_chances
from leakbots import DEFAULT_CONFIG, ActionType, Dir, Game, PipeState, PowerUp, Surface
from leakbots.simulation import run_rounds

ROUNDS = 30
SEEDS = range(100, 100 + ROUNDS)


@pytest.fixture(scope="module")
def solo_games():
    return [Game([TenchijinBot], seed=s).run() for s in SEEDS]


# --- Helpers derived from the rules ------------------------------------------------------------
def test_leak_chances_follow_the_grading_rule():
    chances = leak_chances(180, DEFAULT_CONFIG.n_leaks, DEFAULT_CONFIG.risk_decay)
    assert chances["E"] == 1.0 and chances["A"] == 0.0  # E only for leaks, A only for intact pipes
    assert 0.15 < chances["D"] < 0.35  # about 1 in 4 D squares leaks
    assert chances["E"] > chances["D"] > chances["C"] > chances["B"] > chances["A"]


def test_leak_chances_match_generated_maps():
    leaky = total = 0
    for seed in range(60):
        for row in Game([RandomBot], seed=seed).cells:
            for c in row:
                if c.risk == "D":
                    total += 1
                    leaky += c.pipe_state is PipeState.BROKEN
    expected = leak_chances(180, DEFAULT_CONFIG.n_leaks, DEFAULT_CONFIG.risk_decay)["D"]
    assert abs(leaky / total - expected) < 0.1


def test_dig_payoff_accounts_for_the_moves_left():
    points = {"leak_found": 5, "leak_fixed": 15, "open_hole_penalty": 2}
    assert dig_payoff(0.5, 3, points) == 10  # time to fix and refill
    assert dig_payoff(1.0, 2, points) == 18  # fixed, but the hole stays open
    assert dig_payoff(1.0, 1, points) == 3  # found, hole open
    assert dig_payoff(0.2, 1, points) < 0  # a D square on the last move is not worth it
    assert dig_payoff(1.0, 0, points) == 0


# --- 30 single-player rounds ----------------------------------------------------------------------
def test_no_bot_errors_and_almost_no_invalid_moves(solo_games):
    for g in solo_games:
        state = g.states[0]
        assert state.last_error is None, state.last_error
        assert state.invalid < 0.02 * state.actions


def test_buys_the_satellite_early_in_most_rounds(solo_games):
    bought = [g.bots[0].bought_at for g in solo_games if PowerUp.SATELLITE in g.states[0].upgrades]
    assert len(bought) >= 0.9 * ROUNDS
    assert sorted(bought)[len(bought) // 2] < 100  # median purchase move


def test_fixes_most_e_leaks(solo_games):
    e_cells = [c for g in solo_games for row in g.cells for c in row if c.risk == "E"]
    fixed = sum(c.pipe_state is PipeState.FIXED for c in e_cells)
    assert e_cells and fixed >= 0.9 * len(e_cells)


def test_leaves_no_holes_open(solo_games):
    assert sum(g.states[0].open_holes for g in solo_games) <= 1


def test_scores_far_above_random_bot(solo_games):
    mine = sum(g.states[0].score for g in solo_games) / ROUNDS
    baseline = run_rounds([RandomBot], ROUNDS, seed=SEEDS[0]).seats[0].mean("scores")
    assert mine > baseline + 50


def test_same_seed_same_moves():
    a, b = (Game([TenchijinBot], seed=7).run() for _ in range(2))
    assert [str(e.action) for e in a.history] == [str(e.action) for e in b.history]


# --- Multiplayer ----------------------------------------------------------------------------------
@pytest.mark.parametrize("opponent", [RandomBot, TenchijinBot])
def test_one_vs_one_runs_cleanly(opponent):
    for seed in range(5):
        g = Game([TenchijinBot, opponent], seed=seed).run()
        for bot_id, state in enumerate(g.states):
            assert state.last_error is None, state.last_error
            if isinstance(g.bots[bot_id], TenchijinBot):
                assert state.invalid < 0.02 * state.actions
                assert state.open_holes == 0


# --- Hand-made map: free satellite, known grades --------------------------------------------------
H = Dir.LEFT.bit | Dir.RIGHT.bit
FREE_SATELLITE = replace(DEFAULT_CONFIG, cost_satellite=0)  # bought on the first move


def graded_game(grades, leaks, spawn):
    """Horizontal pipes on every odd row; all squares graded A except `grades` {pos: grade}."""
    masks = [[H if y % 2 else 0 for _ in range(25)] for y in range(25)]
    g = Game([TenchijinBot], FREE_SATELLITE, seed=0, masks=masks, leaks=leaks, spawns=[spawn])
    for y, row in enumerate(g.cells):
        for x, cell in enumerate(row):
            if cell.has_pipe:
                cell.risk = grades.get((x, y), "A")  # test shortcut: grades are read when bought
    return g


def unearthed_squares(g):
    """Squares bot 0 dug, in order."""
    return [e.target for e in g.history if e.result.success and e.action.type is ActionType.UNEARTH]


def test_digs_fixes_and_refills_a_leak():
    leak = (20, 9)
    g = graded_game({leak: "E"}, [leak], spawn=(2, 2)).run()
    state = g.states[0]
    assert unearthed_squares(g) == [leak]
    assert g.cell(leak).pipe_state is PipeState.FIXED and g.cell(leak).surface is Surface.UNTOUCHED
    assert (state.leaks_found, state.leaks_fixed, state.open_holes, state.score) == (1, 1, 0, 20)


def test_refills_an_intact_d_square():
    g = graded_game({(2, 3): "D"}, [(20, 21)], spawn=(2, 2)).run()
    assert unearthed_squares(g)[0] == (2, 3)
    assert g.cell((2, 3)).surface is Surface.UNTOUCHED and g.states[0].open_holes == 0


@pytest.mark.parametrize(
    ("e_square", "first"),
    [((22, 21), (2, 3)), ((5, 3), (5, 3))],  # far E: the adjacent D first; near E: E first
)
def test_weighs_risk_against_distance(e_square, first):
    g = graded_game({(2, 3): "D", e_square: "E"}, [e_square], spawn=(2, 2))
    while not g.over and not unearthed_squares(g):
        g.step()
    assert unearthed_squares(g) == [first]
