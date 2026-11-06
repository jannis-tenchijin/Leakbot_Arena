"""GovernmentBot (bots/government_bot.py): hand-made scenarios plus seeded benchmark rounds.

Scenario maps use the 5x5 layout of tests/test_rules.py (x right, y down): a straight pipe
along row y=2 that leads off both edges, with a leak at (2, 2).
"""

import pytest

from bots.government_bot import GovernmentBot
from bots.random_bot import RandomBot
from leakbots import Action, ActionType, BaseBot, Dir, Game, GameConfig, PipeState, PowerUp, Surface

H = Dir.LEFT.bit | Dir.RIGHT.bit
MASKS = [[0] * 5, [0] * 5, [H] * 5, [0] * 5, [0] * 5]
LEAK = (2, 2)
ROUNDS = 30  # seeded single-player rounds for the statistics tests
DUEL_ROUNDS = 8  # seeded 1 vs 1 rounds


def scenario(spawns, bots=(GovernmentBot,), **config):
    """Play a whole round on the hand-made 5x5 map (cost_ut=0 lets the bot buy UT at once)."""
    cfg = GameConfig(width=5, height=5, n_leaks=1, **{"max_moves": 20, "cost_ut": 0, **config})
    return Game(list(bots), cfg, seed=0, masks=MASKS, leaks=[LEAK], spawns=list(spawns)).run()


def actions(game, bot_id=0):
    return [e.action.type for e in game.history if e.bot_id == bot_id]


def assert_clean(game, bot_id=0):
    """No crash, no invalid move, no open hole of this bot."""
    st = game.states[bot_id]
    assert st.last_error is None
    assert st.invalid == 0, [e.result.message for e in game.history if not e.result.success]
    assert st.open_holes == 0


# --- Scenarios ----------------------------------------------------------------------------------
def test_digs_fixes_and_refills_a_reported_leak():
    g = scenario(spawns=[(2, 1)])  # spawn right above the leak
    t = ActionType
    assert actions(g)[:5] == [t.BUY, t.DETECT, t.UNEARTH, t.FIX, t.REFILL]
    st = g.states[0]
    assert (st.leaks_found, st.leaks_fixed) == (1, 1)
    assert g.cell(LEAK).pipe_state is PipeState.FIXED and g.cell(LEAK).surface is Surface.UNTOUCHED
    assert_clean(g)


def test_steps_off_a_leak_it_stands_on():
    g = scenario(spawns=[LEAK])  # the spawn square was scanned before UT: it must be re-scanned
    assert g.states[0].leaks_fixed == 1
    assert g.cell(LEAK).surface is Surface.UNTOUCHED
    assert_clean(g)


def test_no_dig_without_moves_left_to_refill():
    g = scenario(spawns=[(2, 1)], max_moves=4)  # buy, detect, then only 2 moves: too few to dig
    assert ActionType.UNEARTH not in actions(g)
    assert g.cell(LEAK).surface is Surface.UNTOUCHED
    assert_clean(g)


class DiggerBot(BaseBot):
    """Digs the square below its spawn once, then only detects (leaves the hole open)."""

    def act(self, view):
        if view.move == 0:
            return Action.unearth(Dir.DOWN)
        return Action.detect()


def test_fixes_a_broken_pipe_another_bot_left_open():
    g = scenario(spawns=[(2, 1), (4, 0)], bots=(DiggerBot, GovernmentBot), cost_ut=15)
    gov = g.states[1]
    assert g.states[0].leaks_found == 1  # the digger found it ...
    assert gov.leaks_fixed == 1 and PowerUp.UT in gov.upgrades  # ... the GovernmentBot fixed it
    assert_clean(g, bot_id=1)


def test_same_seed_same_round():
    a, b = (Game([GovernmentBot, RandomBot], seed=11).run() for _ in range(2))
    assert [(e.bot_id, e.action) for e in a.history] == [(e.bot_id, e.action) for e in b.history]
    assert [s.score for s in a.states] == [s.score for s in b.states]


# --- Seeded benchmark rounds ----------------------------------------------------------------------
@pytest.fixture(scope="module")
def solo():
    return [Game([GovernmentBot], seed=seed).run() for seed in range(ROUNDS)]


def test_solo_rounds_run_clean(solo):
    states = [g.states[0] for g in solo]
    assert all(st.last_error is None for st in states)
    assert sum(st.invalid for st in states) < 0.02 * sum(st.actions for st in states)
    assert sum(st.open_holes for st in states) / ROUNDS < 0.1


def test_buys_ut_as_soon_as_it_is_affordable(solo):
    bought = 0
    for g in solo:
        cost, st = g.config.cost_ut, g.states[0]
        # Score before the first move: the spawn square's free auto-detect may already pay +1
        penalty = st.open_holes * g.config.penalty_open_pipe_hole
        score = st.score + penalty - sum(e.result.points for e in g.history)
        for event in g.history:  # single player: every event is a move of bot 0
            if event.action is not None and event.action.type is ActionType.BUY:
                assert score >= cost
                bought += 1
                break
            assert score < cost, f"seed {g.seed}: could afford UT at move {event.turn} but did not buy"
            score += event.result.points
    assert bought >= 0.9 * ROUNDS


def test_finds_and_fixes_leaks(solo):
    states = [g.states[0] for g in solo]
    assert sum(st.leaks_found for st in states) / ROUNDS >= 4
    assert sum(st.leaks_fixed for st in states) / ROUNDS >= 4
    assert sum(st.pipes_detected for st in states) / ROUNDS >= 150


def test_scores_far_above_random_bot(solo):
    gov = sum(g.states[0].score for g in solo) / ROUNDS
    rnd = sum(Game([RandomBot], seed=seed).run().states[0].score for seed in range(ROUNDS)) / ROUNDS
    assert gov >= rnd + 150


@pytest.mark.parametrize("opponent", [RandomBot, GovernmentBot])
def test_duels_run_clean(opponent):
    games = [Game([GovernmentBot, opponent], seed=seed).run() for seed in range(DUEL_ROUNDS)]
    for bot_id in range(2):
        if games[0].bots[bot_id].__class__ is not GovernmentBot:
            continue
        states = [g.states[bot_id] for g in games]
        assert all(st.last_error is None for st in states)
        assert sum(st.invalid for st in states) < 0.02 * sum(st.actions for st in states)
        assert sum(st.open_holes for st in states) == 0
        assert sum(st.score for st in states) / DUEL_ROUNDS > 100
    if opponent is RandomBot:
        assert all(g.winners() == [0] for g in games)
