"""GovernmentBot (bots/government_bot.py): hand-made scenarios plus seeded benchmark rounds.

Scenario maps use the 5x5 layout of tests/test_rules.py (x right, y down): a straight pipe
along row y=2 that leads off both edges, with a leak at (2, 2). Its pipe connects to (1, 2) and
(3, 2), so a repair needs two closed valves.
"""

import pytest

from bots.government_bot import GovernmentBot
from bots.random_bot import RandomBot
from leakbots import Action, ActionType, BaseBot, Dir, Game, GameConfig, PipeState, PowerUp, Surface

H = Dir.LEFT.bit | Dir.RIGHT.bit
MASKS = [[0] * 5, [0] * 5, [H] * 5, [0] * 5, [0] * 5]
LEAK = (2, 2)
VALVES = [(1, 2), (3, 2)]  # the squares the leaking pipe connects to
ROUNDS = 30  # seeded single-player rounds for the statistics tests
DUEL_ROUNDS = 8  # seeded 1 vs 1 rounds
t = ActionType
REPAIR = [t.UNEARTH, t.UNEARTH, t.INSTALL_VALVE, t.CLOSE_VALVE, t.UNEARTH, t.INSTALL_VALVE, t.CLOSE_VALVE,
          t.FIX, t.REFILL, t.OPEN_VALVE, t.REFILL, t.OPEN_VALVE, t.REFILL]  # the full valve repair


def make_game(spawns, bots=(GovernmentBot,), waterworks="auto", **config):
    """Unplayed round on the hand-made 5x5 map (cost_ut=0 lets the bot buy UT at once)."""
    cfg = GameConfig(width=5, height=5, n_leaks=1, **{"max_moves": 40, "cost_ut": 0, **config})
    return Game(
        list(bots), cfg, seed=0, masks=MASKS, leaks=[LEAK], spawns=list(spawns), waterworks=waterworks
    )


def scenario(spawns, bots=(GovernmentBot,), **config):
    """Play a whole round on the hand-made 5x5 map."""
    return make_game(spawns, bots, **config).run()


def actions(game, bot_id=0):
    return [e.action.type for e in game.history if e.bot_id == bot_id]


def work(game, bot_id=0):
    """Actions other than moves and detects."""
    return [a for a in actions(game, bot_id) if a not in (t.MOVE, t.DETECT)]


def assert_clean(game, bot_id=0, holes=0):
    """No crash, no invalid move, no closed valve and no open hole of this bot left at the end."""
    st = game.states[bot_id]
    assert st.last_error is None
    assert st.invalid == 0, [e.result.message for e in game.history if not e.result.success]
    assert st.closed_valves == 0
    assert st.open_holes == holes


def gov_events(game, bot_id=0):
    return [e for e in game.history if e.bot_id == bot_id]


# --- Valve repair -------------------------------------------------------------------------------
def test_full_valve_repair():
    g = scenario(spawns=[(2, 1)])  # spawn right above the leak
    assert work(g)[:14] == [t.BUY] + REPAIR
    st = g.states[0]
    assert (st.leaks_found, st.leaks_fixed, st.valves_reopened) == (1, 1, 2)
    assert st.score == st.pipes_detected + 5 + 40 + 5 * len(VALVES)
    for pos in [LEAK, *VALVES]:
        assert g.cell(pos).surface is Surface.UNTOUCHED and g.cell(pos).valve_open
    assert g.cell(LEAK).pipe_state is PipeState.FIXED
    assert_clean(g)


def test_steps_off_a_leak_it_stands_on():
    g = scenario(spawns=[LEAK])  # the spawn square was scanned before UT: it must be re-scanned
    assert g.states[0].leaks_fixed == 1 and g.states[0].valves_reopened == 2
    assert g.cell(LEAK).surface is Surface.UNTOUCHED
    assert_clean(g)


@pytest.mark.parametrize("max_moves, digs", [(26, False), (30, True)])
def test_only_starts_a_repair_that_fits(max_moves, digs):
    # buy, detect, then the repair needs 21 moves; the bot's estimate is a little cautious
    g = scenario(spawns=[(2, 1)], max_moves=max_moves)
    assert (t.UNEARTH in actions(g)) is digs
    assert g.states[0].leaks_fixed == digs
    assert_clean(g)


def test_cleans_up_when_the_round_ends_mid_repair():
    g = make_game(spawns=[(2, 1)], max_moves=60)
    while not any(g.cell(p).valve and not g.cell(p).valve_open for p in VALVES):
        g.step()  # until the first valve is closed
    g.states[0].chained_left = 100  # suddenly every step costs two moves ...
    g.turn = g.config.max_moves - 9  # ... and only 9 moves are left: the fix no longer fits
    g.run()
    st = g.states[0]
    assert st.leaks_fixed == 0
    assert all(g.cell(p).surface is Surface.UNTOUCHED for p in VALVES)  # valve reopened, holes refilled
    assert_clean(g, holes=1)  # only the leak itself stays open (a broken pipe can't be refilled)


def test_walks_to_the_waterworks_to_buy_ut():
    g = scenario(spawns=[(2, 1)], waterworks=(0, 4), max_moves=100)
    assert actions(g)[:6] == [t.MOVE] * 5 + [t.BUY]  # 5 steps from (2, 1) to (0, 4), then buy there
    buy = next(e for e in g.history if e.action.type is t.BUY)
    assert buy.from_pos == (0, 4) and buy.result.success
    assert g.states[0].leaks_fixed == 1
    assert_clean(g)


def test_finds_a_leak_that_appears_later():
    g = scenario(spawns=[(2, 1)], max_moves=150, extra_leak_moves=(60,))
    assert len(g.leak_positions) == 2
    new = g.leak_positions[1]
    assert new in [(0, 2), (4, 2)]  # the only buried pipe squares nobody had dug up
    # Its last UT reading (before move 60) was stale: the bot scanned it again and repaired it
    st = g.states[0]
    assert st.leaks_found == st.leaks_fixed == 2
    assert st.valves_reopened == 3  # an edge square: its other link leaves the map
    assert_clean(g)


class DiggerBot(BaseBot):
    """Digs the square below its spawn once, then only detects (leaves the hole open)."""

    def act(self, view):
        if view.move == 0:
            return Action.unearth(Dir.DOWN)
        return Action.detect()


def test_repairs_a_broken_pipe_another_bot_left_open():
    g = scenario(spawns=[(2, 1), (4, 0)], bots=(DiggerBot, GovernmentBot), cost_ut=15, max_moves=60)
    gov = g.states[1]
    assert g.states[0].leaks_found == 1  # the digger found it ...
    assert gov.leaks_fixed == 1 and gov.valves_reopened == 2  # ... the GovernmentBot repaired it
    assert PowerUp.UT in gov.upgrades
    assert_clean(g, bot_id=1)


# --- Punk upgrade -------------------------------------------------------------------------------
class DetectBot(BaseBot):
    """Stands still and only detects (a target for the ball and chain)."""

    def act(self, view):
        return Action.detect()


class PacerBot(BaseBot):
    """Walks back and forth along its row (a moving target for the chase)."""

    def act(self, view):
        d = Dir.RIGHT if (view.move // 6) % 2 == 0 else Dir.LEFT
        return Action.move(d) if view.is_walkable(view.neighbor(d)) else Action.detect()


def open_field(bots, spawns, **config):
    """A 9x9 map without pipes for chase tests."""
    cfg = GameConfig(width=9, height=9, n_leaks=0, **{"max_moves": 60, "punk_spawn_move": 0, **config})
    return Game(list(bots), cfg, seed=0, masks=[[0] * 9 for _ in range(9)], leaks=[], spawns=list(spawns))


def test_collects_a_nearby_punk_pickup():
    g = make_game(spawns=[(0, 4)], max_moves=60)
    g.cell((0, 1)).item = "punk"  # 3 steps straight up, no job known yet
    g.punk_pos = (0, 1)
    g.run()
    st = g.states[0]
    assert PowerUp.PUNK in st.upgrades and st.bonus == 10 and g.punk_pos is None
    assert actions(g)[:4] == [t.BUY] + [t.MOVE] * 3  # straight there after buying UT
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


def test_chases_and_chains_a_moving_opponent():
    g = open_field([GovernmentBot, PacerBot], spawns=[(0, 0), (4, 8)])
    g.states[0].upgrades.add(PowerUp.PUNK)
    g.run()
    chain = next(e for e in g.history if e.action.type is t.CHAIN)
    assert chain.result.success and chain.turn < 2 * 20  # caught it within 20 moves
    assert all(a is t.MOVE for a in actions(g)[: chain.turn // 2])  # straight after it
    assert g.states[1].chained_left > 0
    assert_clean(g)


def test_chases_the_leader_in_a_battle_royale():
    g = open_field([GovernmentBot, DetectBot, DetectBot], spawns=[(4, 4), (3, 4), (8, 8)])
    g.states[0].upgrades.add(PowerUp.PUNK)
    g.states[1].score, g.states[2].score = 5, 20  # the far bot leads: skip the adjacent one
    g.run()
    assert g.states[2].chained_left > 0 and g.states[1].chained_left == 0
    assert actions(g)[0] is t.MOVE


def test_gives_up_the_chase_at_the_end_of_the_round():
    g = open_field([GovernmentBot, DetectBot], spawns=[(0, 0), (8, 8)], max_moves=15)
    g.states[0].upgrades.add(PowerUp.PUNK)
    g.run()
    assert not g.states[0].chain_used  # fewer than 20 moves left: no chase, normal work instead
    assert g.states[0].pos[0] + g.states[0].pos[1] < 8  # it did not walk across to the opponent


def test_finishes_its_own_hole_before_chaining_late_in_the_round():
    g = make_game(spawns=[(2, 1), (3, 1)], bots=(GovernmentBot, DetectBot), max_moves=60)
    for _ in range(3 * 2):  # BUY, DETECT, UNEARTH (each followed by a move of the DetectBot)
        g.step()
    assert g.cell(LEAK).surface is Surface.UNEARTHED
    g.states[0].upgrades.add(PowerUp.PUNK)  # becomes a punk with its own hole open
    g.run()
    assert work(g)[:15] == [t.BUY] + REPAIR + [t.CHAIN]
    assert g.states[1].chained_left > 0
    assert_clean(g)


def test_chains_mid_repair_early_in_the_round():
    g = make_game(spawns=[(2, 1), (3, 1)], bots=(GovernmentBot, DetectBot), max_moves=400,
                  extra_leak_moves=(), punk_spawn_move=0)
    for _ in range(3 * 2):
        g.step()
    g.states[0].upgrades.add(PowerUp.PUNK)
    g.run()
    assert work(g)[:4] == [t.BUY, t.UNEARTH, t.CHAIN, t.UNEARTH]  # chains at once, then repairs on
    assert g.states[0].leaks_fixed == 1
    assert_clean(g)


def test_chained_bot_still_finishes_its_jobs():
    g = make_game(spawns=[(4, 0)], max_moves=80)
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


@pytest.mark.parametrize("chained, moves_left, digs", [(0, 32, True), (100, 32, False), (100, 46, True)])
def test_chained_travel_counts_two_moves_per_step(chained, moves_left, digs):
    g = make_game(spawns=[(2, 0)], cost_ut=999, max_moves=200)  # no UT purchase: only the time math counts
    g.bots[0].leaks = {LEAK}  # the bot already knows the leak: 1 step to (2, 1), then the repair
    g.states[0].chained_left = chained
    g.turn = g.config.max_moves - moves_left
    g.run()
    assert (t.UNEARTH in actions(g)) is digs
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
    assert sum(st.closed_valves for st in states) / ROUNDS < 0.1


def test_walks_to_the_waterworks_once_ut_is_affordable(solo):
    bought = 0
    for g in solo:
        cost, st, ww = g.config.cost_ut, g.states[0], g.waterworks_pos
        # Score before the first move: the spawn square's free auto-detect may already pay +1
        cfg = g.config
        penalty = st.open_holes * cfg.penalty_open_pipe_hole + st.closed_valves * cfg.penalty_closed_valve
        score = st.score + penalty - sum(e.result.points for e in g.history)
        trip = None  # moves since UT became affordable, and the distance to the waterworks then
        for event in g.history:  # single player: every event is a move of bot 0
            if trip is None and score >= cost:
                pos = event.from_pos
                trip = [0, abs(pos[0] - ww[0]) + abs(pos[1] - ww[1])]
            if event.action.type is t.BUY:
                assert trip is not None and event.from_pos == ww
                assert trip[0] == trip[1], f"seed {g.seed}: took {trip[0]} moves for a {trip[1]}-step walk"
                bought += 1
                break
            if trip is not None:
                assert event.action.type is t.MOVE, f"seed {g.seed}: dawdled on the way to the waterworks"
                trip[0] += 1
            score += event.result.points
    assert bought >= 0.9 * ROUNDS


def test_finds_and_fixes_leaks(solo):
    states = [g.states[0] for g in solo]
    assert all(len(g.leak_positions) == 8 for g in solo)  # 5 at the start + 3 new ones
    assert sum(st.leaks_found for st in states) / ROUNDS >= 4
    assert sum(st.leaks_fixed for st in states) / ROUNDS >= 4
    assert sum(st.valves_reopened for st in states) >= 1.9 * sum(st.leaks_fixed for st in states)
    assert sum(st.pipes_detected for st in states) / ROUNDS >= 110


def test_repairs_leaks_that_appear_during_the_round(solo):
    late = [p for g in solo for p in g.leak_positions[5:] if g.cell(p).pipe_state is PipeState.FIXED]
    assert len(late) >= 0.3 * 3 * ROUNDS


def test_scores_far_above_random_bot(solo):
    gov = sum(g.states[0].score for g in solo) / ROUNDS
    rnd = sum(Game([RandomBot], seed=seed).run().states[0].score for seed in range(ROUNDS)) / ROUNDS
    assert gov >= rnd + 250


_duels: dict[type, list[Game]] = {}


def duels(opponent):
    """Seeded 1 vs 1 rounds against an opponent, played once per test session."""
    if opponent not in _duels:
        _duels[opponent] = [Game([GovernmentBot, opponent], seed=seed).run() for seed in range(DUEL_ROUNDS)]
    return _duels[opponent]


@pytest.mark.parametrize("opponent", [RandomBot, GovernmentBot])
def test_duels_run_clean(opponent):
    games = duels(opponent)
    for bot_id in range(2):
        if games[0].bots[bot_id].__class__ is not GovernmentBot:
            continue
        states = [g.states[bot_id] for g in games]
        assert all(st.last_error is None for st in states)
        assert sum(st.invalid for st in states) < 0.02 * sum(st.actions for st in states)
        assert sum(st.open_holes for st in states) / DUEL_ROUNDS < 0.2
        assert sum(st.closed_valves for st in states) == 0
        assert sum(st.score for st in states) / DUEL_ROUNDS > 100
    if opponent is RandomBot:
        assert all(g.winners() == [0] for g in games)
    else:  # the punk rules are exercised: one bot always takes the pickup, and chains happen
        assert all(any(PowerUp.PUNK in st.upgrades for st in g.states) for g in games)
        assert any(st.chain_used for g in games for st in g.states)


def test_punks_chase_and_chain_in_duels():
    games = duels(RandomBot)
    punks = [g.states[0] for g in games if PowerUp.PUNK in g.states[0].upgrades]
    assert len(punks) >= DUEL_ROUNDS // 2 and all(st.chain_used for st in punks)


def test_collects_the_punk_pickup_in_most_rounds(solo):
    assert sum(PowerUp.PUNK in g.states[0].upgrades for g in solo) >= 0.8 * ROUNDS
