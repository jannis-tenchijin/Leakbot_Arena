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


# --- Punk upgrade -------------------------------------------------------------------------------
class DetectBot(BaseBot):
    """Stands still and only detects (a target for the ball and chain)."""

    def act(self, view):
        return Action.detect()


def make_game(spawns, bots=(GovernmentBot,), **config):
    """Like scenario(), but returns the unplayed Game so a test can change it first."""
    cfg = GameConfig(width=5, height=5, n_leaks=1, **{"max_moves": 20, "cost_ut": 0, **config})
    return Game(list(bots), cfg, seed=0, masks=MASKS, leaks=[LEAK], spawns=list(spawns))


def gov_events(game, bot_id=0):
    return [e for e in game.history if e.bot_id == bot_id]


def test_collects_a_nearby_punk_pickup():
    g = make_game(spawns=[(0, 4)])
    g.cell((0, 1)).item = "punk"  # 3 steps straight up, no job known yet
    g.punk_pos = (0, 1)
    g.run()
    st = g.states[0]
    assert PowerUp.PUNK in st.upgrades and st.bonus == 10 and g.punk_pos is None
    assert actions(g)[:4] == [ActionType.BUY] + [ActionType.MOVE] * 3  # straight there after buying UT
    assert st.leaks_fixed == 1  # ... and still does its job afterwards
    assert_clean(g)


def test_never_digs_under_the_pickup():
    g = make_game(spawns=[(2, 1)], max_moves=6)
    g.cell(LEAK).item = "punk"  # the pickup blocks the leak square: take it first, then dig
    g.punk_pos = LEAK
    g.run()
    assert PowerUp.PUNK in g.states[0].upgrades
    assert_clean(g)


def test_chains_the_strongest_adjacent_opponent():
    g = make_game(spawns=[(1, 0), (0, 0), (2, 0)], bots=(GovernmentBot, DetectBot, DetectBot))
    g.states[0].upgrades.add(PowerUp.PUNK)
    g.states[1].score, g.states[2].score = 5, 20  # the bot on the right leads
    event = g.step()
    assert event.action == Action.chain(Dir.RIGHT) and event.result.success
    assert g.states[2].chained_left == 100 and g.states[1].chained_left == 0


def test_finishes_its_own_hole_before_chaining():
    g = make_game(spawns=[(2, 1), (3, 1)], bots=(GovernmentBot, DetectBot))
    for _ in range(3 * 2):  # BUY, DETECT, UNEARTH (each followed by a move of the DetectBot)
        g.step()
    assert g.cell(LEAK).surface is Surface.UNEARTHED
    g.states[0].upgrades.add(PowerUp.PUNK)  # becomes a punk with its own hole open
    g.run()
    t = ActionType
    assert actions(g)[:6] == [t.BUY, t.DETECT, t.UNEARTH, t.FIX, t.REFILL, t.CHAIN]
    assert g.states[1].chained_left > 0
    assert_clean(g)


def test_chained_bot_still_finishes_its_jobs():
    g = make_game(spawns=[(4, 0)], max_moves=40)
    g.states[0].chained_left = 100
    g.run()
    st = g.states[0]
    assert st.leaks_fixed == 1 and g.cell(LEAK).surface is Surface.UNTOUCHED
    assert_clean(g)
    # It keeps dragging one way: every drag is followed by the step in the same direction
    events = gov_events(g)
    drags = [i for i, e in enumerate(events) if e.result.message.startswith("dragging")]
    assert drags
    for i in drags:
        nxt = events[i + 1]
        assert nxt.action == events[i].action and nxt.from_pos != nxt.target and not nxt.result.message


@pytest.mark.parametrize("chained, moves_left, digs", [(0, 4, True), (100, 4, False), (100, 5, True)])
def test_chained_travel_counts_two_moves_per_step(chained, moves_left, digs):
    g = make_game(spawns=[(2, 0)], cost_ut=999)  # no UT purchase: only the travel math counts
    g.bots[0].leaks = {LEAK}  # the bot already knows the leak: 1 step to (2, 1), then 3 moves of digging
    g.states[0].chained_left = chained
    g.turn = g.config.max_moves - moves_left
    g.run()
    assert (ActionType.UNEARTH in actions(g)) is digs
    assert g.states[0].leaks_fixed == digs
    assert_clean(g)


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
    else:  # the punk rules are exercised: one bot always takes the pickup, and chains happen
        assert all(any(PowerUp.PUNK in st.upgrades for st in g.states) for g in games)
        assert any(st.chain_used for g in games for st in g.states)


def test_collects_the_punk_pickup_in_most_rounds(solo):
    assert sum(PowerUp.PUNK in g.states[0].upgrades for g in solo) >= 0.8 * ROUNDS
