"""TenchijinBot: buys the satellite, digs D/E squares, fixes leaks, leaves no holes open.

The statistics run 30 seeded single-player rounds once (module fixture); the scenario tests
use a hand-made 25x25 map with a free satellite so the target order is known. Punk scenarios
place the pickup by hand and set upgrades / chains directly on the bot states.
"""

from dataclasses import replace

import pytest

from bots.random_bot import RandomBot
from bots.tenchijin_bot import TenchijinBot, dig_payoff, leak_chances
from leakbots import DEFAULT_CONFIG, Action, ActionType, BaseBot, Dir, Game, PipeState, PowerUp, Surface
from leakbots.model import PUNK_ITEM
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


def test_collects_the_punk_pickup_in_most_rounds(solo_games):
    # Alone, the satellite targets run out around move 200-250, so the pickup is nearly free
    collected = sum(PowerUp.PUNK in g.states[0].upgrades for g in solo_games)
    assert collected >= 0.9 * ROUNDS


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
# Satellite bought on the first move; no random punk pickup (the punk tests place it by hand)
FREE_SATELLITE = replace(DEFAULT_CONFIG, cost_satellite=0, punk_spawn_move=0)


class StillBot(BaseBot):
    """Opponent that never moves (detect is always valid)."""

    def act(self, view):
        return Action.detect()


def graded_game(grades, leaks, spawn, opponents=(), config=FREE_SATELLITE):
    """Horizontal pipes on every odd row; all squares graded A except `grades` {pos: grade}.

    TenchijinBot is bot 0 at `spawn`; one StillBot stands on each square in `opponents`.
    """
    masks = [[H if y % 2 else 0 for _ in range(25)] for y in range(25)]
    bots = [TenchijinBot] + [StillBot] * len(opponents)
    g = Game(bots, config, seed=0, masks=masks, leaks=leaks, spawns=[spawn, *opponents])
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


# --- Punk pickup ----------------------------------------------------------------------------------
def drop_punk(g, pos):
    g.cell(pos).item = PUNK_ITEM
    g.punk_pos = pos


def bot0_events(g):
    return [e for e in g.history if e.bot_id == 0]


def collected_at(g):
    """Index (among bot 0's moves) of the move that picked up the punk, or None."""
    for i, e in enumerate(bot0_events(g)):
        if e.result.success and e.action.type is ActionType.MOVE and e.result.points >= 10:
            return i
    return None


@pytest.mark.parametrize(
    ("punk", "pickup_first"),
    [((5, 2), True), ((22, 22), False)],  # near pickup: detour first; far one: the near E first
)
def test_weighs_the_pickup_against_distance(punk, pickup_first):
    leak = (5, 5)
    g = graded_game({leak: "E"}, [leak], spawn=(2, 2))
    drop_punk(g, punk)
    g.run()
    state = g.states[0]
    assert PowerUp.PUNK in state.upgrades and g.punk_pos is None  # collected either way
    dug_at = next(i for i, e in enumerate(bot0_events(g)) if e.action.type is ActionType.UNEARTH)
    assert (collected_at(g) < dug_at) == pickup_first
    assert (state.leaks_fixed, state.open_holes, state.score, state.invalid) == (1, 0, 30, 0)


def test_collects_a_pickup_lying_on_its_target_then_digs_it():
    leak = (2, 3)  # the pickup blocks digging here: step onto it first, then dig from a neighbour
    g = graded_game({leak: "E"}, [leak], spawn=(2, 2))
    drop_punk(g, leak)
    g.run()
    state = g.states[0]
    assert PowerUp.PUNK in state.upgrades and unearthed_squares(g) == [leak]
    assert (state.leaks_fixed, state.open_holes, state.score, state.invalid) == (1, 0, 30, 0)


# --- Ball and chain -------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("scores", "chained", "victim"),
    [
        ((0, 5, 20), (0, 0), (1, 2)),  # the stronger opponent (bot 2, on the left)
        ((0, 20, 5), (0, 0), (3, 2)),  # the stronger opponent (bot 1, on the right)
        ((0, 5, 20), (0, 50), (3, 2)),  # bot 2 is already chained: chain bot 1
    ],
)
def test_chains_the_strongest_adjacent_opponent(scores, chained, victim):
    g = graded_game({}, [(20, 21)], spawn=(2, 2), opponents=[(3, 2), (1, 2)])
    g.states[0].upgrades.add(PowerUp.PUNK)
    for i, s in enumerate(scores):
        g.states[i].score = s
    g.states[1].chained_left, g.states[2].chained_left = chained
    event = g.step()
    assert event.action.type is ActionType.CHAIN and event.target == victim and event.result.success
    assert g.states[g.cell(victim).occupant].chained_left == DEFAULT_CONFIG.chain_duration


def test_finishes_its_own_hole_before_chaining():
    leak = (2, 3)
    g = graded_game({leak: "E"}, [leak], spawn=(2, 2), opponents=[(1, 2)])
    while not unearthed_squares(g):
        g.step()
    g.states[0].upgrades.add(PowerUp.PUNK)  # becomes a punk with its leak dug open
    while len(bot0_events(g)) < 5:
        g.step()
    kinds = [e.action.type for e in bot0_events(g)[2:5]]  # after buy + unearth
    assert kinds == [ActionType.FIX, ActionType.REFILL, ActionType.CHAIN]
    assert g.states[1].chained_left > 0


def test_no_chain_without_punk_or_neighbour():
    g = graded_game({}, [(20, 21)], spawn=(2, 2), opponents=[(4, 2)])
    g.states[0].upgrades.add(PowerUp.PUNK)  # punk, but the opponent is two squares away
    assert g.step().action.type is not ActionType.CHAIN
    g = graded_game({}, [(20, 21)], spawn=(2, 2), opponents=[(3, 2)])  # adjacent, but no punk
    assert g.step().action.type is not ActionType.CHAIN


# --- Chained: two moves per step ------------------------------------------------------------------
def assert_drags_then_steps(g):
    """Every drag of bot 0 is completed by a MOVE in the same direction right after."""
    events = bot0_events(g)
    for a, b in zip(events, events[1:]):
        if "dragging" in a.result.message:
            assert b.action.type is ActionType.MOVE and b.action.direction is a.action.direction
            assert b.result.success and b.target == a.target


def test_still_digs_fixes_and_refills_when_chained():
    leak = (20, 9)
    g = graded_game({leak: "E"}, [leak], spawn=(2, 2))
    g.states[0].chained_left = 100
    g.run()
    state = g.states[0]
    assert unearthed_squares(g) == [leak]
    assert g.cell(leak).pipe_state is PipeState.FIXED and g.cell(leak).surface is Surface.UNTOUCHED
    assert (state.leaks_fixed, state.open_holes, state.score, state.invalid) == (1, 0, 20, 0)
    assert any("dragging" in e.result.message for e in bot0_events(g))
    assert_drags_then_steps(g)


@pytest.mark.parametrize("max_moves", range(78, 89))
def test_chained_end_of_round_leaves_no_hole_open(max_moves):
    # D square 40 steps away = 80 chained moves after the buy: digging (unearth + refill) only
    # fits from max_moves 83 on; before that the bot must not even start
    d_square = (22, 23)
    config = replace(FREE_SATELLITE, max_moves=max_moves)
    g = graded_game({d_square: "D"}, [(20, 21)], spawn=(2, 2), config=config)
    g.states[0].chained_left = 100
    g.run()
    state = g.states[0]
    assert unearthed_squares(g) == ([d_square] if max_moves >= 83 else [])
    assert (state.open_holes, state.invalid) == (0, 0)
    assert_drags_then_steps(g)


@pytest.mark.parametrize(("chained", "first"), [(0, (8, 3)), (100, (2, 3))])
def test_chained_travel_counts_double_in_the_scoring(chained, first):
    # E 6 steps away: 20 / (6 + 3) beats the adjacent D (~1.5 points per move) when free,
    # but not with 12 moves of travel when chained
    g = graded_game({(2, 3): "D", (8, 3): "E"}, [(8, 3)], spawn=(2, 2))
    g.states[0].chained_left = chained
    while not g.over and not unearthed_squares(g):
        g.step()
    assert unearthed_squares(g) == [first]


@pytest.mark.parametrize("drag", [Dir.RIGHT, Dir.DOWN])
def test_completes_a_half_dragged_step_when_the_ways_tie(drag):
    # The D square at (6, 5) is as near via the right as via below: keep the started drag
    g = graded_game({(6, 5): "D"}, [(20, 21)], spawn=(2, 2))
    g.states[0].chained_left = 100
    g.step()  # buys the satellite
    g.states[0].dragging = drag  # pretend the bot already dragged one way
    event = g.step()
    assert event.action.type is ActionType.MOVE and event.action.direction is drag
    assert g.states[0].pos == drag.step((2, 2))  # the second MOVE that way steps


def test_drops_a_drag_that_is_no_longer_useful():
    g = graded_game({(12, 3): "D"}, [(20, 21)], spawn=(2, 2))
    g.states[0].chained_left = 100
    g.step()  # buys the satellite
    g.states[0].dragging = Dir.UP  # away from the target
    event = g.step()
    assert event.action == Action.move(Dir.RIGHT) and g.states[0].pos == (2, 2)  # a new drag
    assert g.states[0].dragging is Dir.RIGHT
