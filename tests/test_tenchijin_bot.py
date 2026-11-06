"""TenchijinBot: buys the satellite at the waterworks, digs B+ squares, repairs leaks with valves,
keeps rechecking after its last leak, chases opponents as a punk, leaves no holes or valves behind.

The statistics run 30 seeded single-player rounds once (module fixture); the scenario tests
use a hand-made 25x25 map (no waterworks unless given) with a free satellite so the target order
is known. Punk scenarios place the pickup by hand and set upgrades / chains directly on the bot states.
"""

from dataclasses import replace

import pytest

from bots.random_bot import RandomBot
from bots.tenchijin_bot import TenchijinBot, dig_payoff, leak_chances, repair_moves
from leakbots import (
    DEFAULT_CONFIG, Action, ActionType, BaseBot, BotView, Dir, Game, PipeState, PowerUp, Surface,
)
from leakbots.model import PUNK_ITEM
from leakbots.simulation import run_rounds

ROUNDS = 30
SEEDS = range(100, 100 + ROUNDS)


@pytest.fixture(scope="module")
def solo_games():
    return [Game([TenchijinBot], seed=s).run() for s in SEEDS]


def bot0_events(g):
    return [e for e in g.history if e.bot_id == 0]


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
    points = {"leak_found": 5, "leak_fixed": 40, "open_hole_penalty": 2, "valve_reopened": 5}
    assert repair_moves(2) == 19 and repair_moves(2, walk=2) == 25  # 5 per valve + walking + 3
    assert dig_payoff(0.5, 19, points, valves=2) == 27.5  # full repair: 5 + 40 + 2 * 5
    assert dig_payoff(1.0, 19, points, valves=3) == 3  # 3 valves need 27 moves: found, hole open
    assert dig_payoff(1.0, 24, points, valves=2, walk=2) == 3  # chained: the repair needs 25
    assert dig_payoff(1.0, 18, points, valves=2) == 3  # no time to repair: found, hole open
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
    for g in solo_games:  # always standing on the waterworks
        buys = [e for e in bot0_events(g) if e.action.type is ActionType.BUY]
        assert all(e.result.success and e.from_pos == g.waterworks_pos for e in buys)


def test_fixes_most_e_leaks(solo_games):
    e_cells = [c for g in solo_games for row in g.cells for c in row if c.risk == "E"]
    fixed = sum(c.pipe_state is PipeState.FIXED for c in e_cells)
    assert e_cells and fixed >= 0.9 * len(e_cells)


def test_repairs_most_leaks_including_new_ones(solo_games):
    # 5 leaks at the start + one at each of moves 200, 300, 400
    total = sum(len(g.leak_positions) for g in solo_games)
    fixed = sum(g.states[0].leaks_fixed for g in solo_games)
    reopened = sum(g.states[0].valves_reopened for g in solo_games)
    assert total == 8 * ROUNDS and fixed >= 0.9 * total and reopened >= 1.8 * fixed


def test_leaves_no_intact_holes_and_no_closed_valves(solo_games):
    # The only holes left open are leaks it cannot repair (found late, or no square to stand on
    # for the fix): +5 found - 2 penalty. Every valve it closed is open again.
    for g in solo_games:
        holes = [c for row in g.cells for c in row if c.surface is Surface.UNEARTHED and c.has_pipe]
        assert all(c.pipe_state is PipeState.BROKEN for c in holes)
        assert g.states[0].closed_valves == 0
    assert sum(g.states[0].open_holes for g in solo_games) <= 0.2 * ROUNDS


def test_keeps_working_after_its_last_fix(solo_games):
    # The old bot detected for 0 points in ~2/3 of its moves after its last fix; now it rechecks
    idle = after = 0
    for g in solo_games:
        events = bot0_events(g)
        fixes = [i for i, e in enumerate(events) if e.action.type is ActionType.FIX and e.result.success]
        last_fix = max(fixes)
        rest = events[last_fix + 1:]
        after += len(rest)
        idle += sum(e.action.type is ActionType.DETECT and e.result.points == 0 for e in rest)
    assert idle < 0.2 * after


def test_collects_the_punk_pickup_in_most_rounds(solo_games):
    collected = sum(PowerUp.PUNK in g.states[0].upgrades for g in solo_games)
    assert collected >= 0.9 * ROUNDS


def test_scores_far_above_random_bot(solo_games):
    mine = sum(g.states[0].score for g in solo_games) / ROUNDS
    baseline = run_rounds([RandomBot], ROUNDS, seed=SEEDS[0]).seats[0].mean("scores")
    assert mine > baseline + 200


def test_same_seed_same_moves():
    a, b = (Game([TenchijinBot], seed=7).run() for _ in range(2))
    assert [str(e.action) for e in a.history] == [str(e.action) for e in b.history]


# --- Multiplayer ----------------------------------------------------------------------------------
@pytest.mark.parametrize("opponent", [RandomBot, TenchijinBot])
def test_one_vs_one_runs_cleanly(opponent):
    chains = 0
    for seed in range(5):
        g = Game([TenchijinBot, opponent], seed=seed).run()
        for bot_id, state in enumerate(g.states):
            assert state.last_error is None, state.last_error
            if isinstance(g.bots[bot_id], TenchijinBot):
                assert state.invalid < 0.02 * state.actions
                assert state.closed_valves == 0
                holes = [c for row in g.cells for c in row
                         if c.surface is Surface.UNEARTHED and c.dug_by == bot_id]
                assert all(c.pipe_state is PipeState.BROKEN for c in holes)  # only unrepairable leaks
                chains += state.chain_used
    assert chains >= 3  # punks chase their opponent down


@pytest.mark.parametrize("seed", range(3))
def test_battle_royale_runs_cleanly(seed):
    g = Game([TenchijinBot, RandomBot, TenchijinBot], seed=seed).run()
    for bot_id in (0, 2):
        state = g.states[bot_id]
        assert state.last_error is None and state.invalid < 0.02 * state.actions and state.closed_valves == 0


# --- Hand-made map: free satellite, known grades --------------------------------------------------
H = Dir.LEFT.bit | Dir.RIGHT.bit
# Satellite bought on the first move; no random punk pickup (the punk tests place it by hand)
FREE_SATELLITE = replace(DEFAULT_CONFIG, cost_satellite=0, punk_spawn_move=0)


class StillBot(BaseBot):
    """Opponent that never moves (detect is always valid)."""

    def act(self, view):
        return Action.detect()


def graded_game(grades, leaks, spawn, opponents=(), config=FREE_SATELLITE, waterworks=None):
    """Horizontal pipes on every odd row; all squares graded A except `grades` {pos: grade}.

    TenchijinBot is bot 0 at `spawn`; one StillBot stands on each square in `opponents`.
    A leak in the middle of a row connects to its left and right squares: two valves.
    """
    masks = [[H if y % 2 else 0 for _ in range(25)] for y in range(25)]
    bots = [TenchijinBot] + [StillBot] * len(opponents)
    spawns = [spawn, *opponents]
    g = Game(bots, config, seed=0, masks=masks, leaks=leaks, spawns=spawns, waterworks=waterworks)
    for y, row in enumerate(g.cells):
        for x, cell in enumerate(row):
            if cell.has_pipe:
                cell.risk = grades.get((x, y), "A")  # test shortcut: grades are read when bought
    return g


def unearthed_squares(g):
    """Squares bot 0 dug, in order."""
    return [e.target for e in g.history if e.result.success and e.action.type is ActionType.UNEARTH]


def assert_clean_repair(g, leak):
    """The leak is fixed and every square of the repair is refilled with its valve open again."""
    squares = [leak, *g.connected_neighbors(leak)]
    assert g.cell(leak).pipe_state is PipeState.FIXED
    assert all(g.cell(p).surface is Surface.UNTOUCHED for p in squares)
    assert all(g.cell(p).valve and g.cell(p).valve_open for p in squares[1:])
    state = g.states[0]
    assert (state.open_holes, state.closed_valves, state.valves_reopened) == (0, 0, 2)


def test_full_valve_repair():
    leak = (20, 9)
    g = graded_game({leak: "E"}, [leak], spawn=(2, 2)).run()
    state = g.states[0]
    assert unearthed_squares(g)[0] == leak and set(unearthed_squares(g)) == {leak, (19, 9), (21, 9)}
    assert_clean_repair(g, leak)
    # 5 found + 40 fixed + 2 * 5 reopened, no invalid move
    assert (state.leaks_found, state.leaks_fixed, state.score, state.invalid) == (1, 1, 55, 0)
    kinds = [e.action.type for e in bot0_events(g)]
    kinds = [k for k in kinds if k not in (ActionType.MOVE, ActionType.DETECT)]
    fix = kinds.index(ActionType.FIX)
    assert kinds[:fix].count(ActionType.CLOSE_VALVE) == 2  # both valves closed before the fix
    assert kinds[fix + 1:].count(ActionType.OPEN_VALVE) == 2  # and reopened after it
    assert kinds.count(ActionType.REFILL) == 3


def test_refills_an_intact_d_square():
    g = graded_game({(2, 3): "D"}, [(20, 21)], spawn=(2, 2)).run()
    assert unearthed_squares(g)[0] == (2, 3)
    assert g.cell((2, 3)).surface is Surface.UNTOUCHED and g.states[0].open_holes == 0
    assert not g.cell((1, 3)).revealed and not g.cell((3, 3)).revealed  # intact: no valves needed


def test_leaves_a_corner_leak_it_cannot_fix():
    # The pipe turns at the corner: both squares next to it must be dug for valves, so there is
    # nowhere left to stand for the fix. Found (+5), left open (-2), never valved in vain.
    masks = [[0] * 25 for _ in range(25)]
    masks[0][23], masks[0][24], masks[1][24] = H, Dir.LEFT.bit | Dir.DOWN.bit, Dir.UP.bit | Dir.DOWN.bit
    for y in range(2, 25):
        masks[y][24] = Dir.UP.bit | (Dir.DOWN.bit if y < 24 else 0)
    masks[0][22] = Dir.RIGHT.bit | Dir.DOWN.bit
    for y in range(1, 25):
        masks[y][22] = Dir.UP.bit | (Dir.DOWN.bit if y < 24 else 0)
    g = Game([TenchijinBot], FREE_SATELLITE, seed=0, masks=masks, leaks=[(24, 0)], spawns=[(20, 2)])
    for row in g.cells:
        for c in row:
            if c.has_pipe:
                c.risk = "A"
    g.cell((24, 0)).risk = "E"
    g.run()
    state = g.states[0]
    assert unearthed_squares(g) == [(24, 0)]
    assert (state.leaks_found, state.open_holes, state.closed_valves, state.invalid) == (1, 1, 0, 0)


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
    assert (state.leaks_fixed, state.open_holes, state.score, state.invalid) == (1, 0, 65, 0)


def test_collects_a_pickup_lying_on_its_target_then_digs_it():
    leak = (2, 3)  # the pickup blocks digging here: step onto it first, then dig from a neighbour
    g = graded_game({leak: "E"}, [leak], spawn=(2, 2))
    drop_punk(g, leak)
    g.run()
    state = g.states[0]
    assert PowerUp.PUNK in state.upgrades and unearthed_squares(g)[0] == leak
    assert_clean_repair(g, leak)
    assert (state.leaks_fixed, state.score, state.invalid) == (1, 65, 0)


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


def test_chains_an_adjacent_opponent_mid_repair_then_finishes_it():
    leak = (2, 3)
    g = graded_game({leak: "E"}, [leak], spawn=(2, 2), opponents=[(4, 2)])
    while not unearthed_squares(g):
        g.step()
    g.states[0].upgrades.add(PowerUp.PUNK)  # becomes a punk with its leak dug open ...
    g.cell(g.states[1].pos).occupant = None
    g.states[1].pos = (2, 1)  # ... and the opponent next to it
    g.cell((2, 1)).occupant = 1
    while g.current_bot_id != 0:
        g.step()
    assert g.step().action.type is ActionType.CHAIN and g.states[1].chained_left > 0
    g.run()
    assert_clean_repair(g, leak)


def test_finishes_a_started_repair_before_chasing():
    leak = (5, 3)
    g = graded_game({leak: "E"}, [leak], spawn=(5, 2), opponents=[(20, 20)])
    while not unearthed_squares(g):
        g.step()
    g.states[0].upgrades.add(PowerUp.PUNK)  # a punk now, with its leak dug open and the prey far away
    g.run()
    kinds = [e.action.type for e in bot0_events(g)]
    assert kinds.index(ActionType.FIX) < kinds.index(ActionType.CHAIN)  # repair first, then the chase
    assert g.states[0].chain_used
    assert_clean_repair(g, leak)


def test_no_chain_without_punk_or_neighbour():
    g = graded_game({}, [(20, 21)], spawn=(2, 2), opponents=[(4, 2)])
    g.states[0].upgrades.add(PowerUp.PUNK)  # punk, but the opponent is two squares away
    assert g.step().action.type is not ActionType.CHAIN
    g = graded_game({}, [(20, 21)], spawn=(2, 2), opponents=[(3, 2)])  # adjacent, but no punk
    assert g.step().action.type is not ActionType.CHAIN


# --- Punk chase -----------------------------------------------------------------------------------
def test_punk_chases_and_chains_its_opponent_then_works_on():
    leak = (5, 21)
    g = graded_game({leak: "E"}, [leak], spawn=(2, 2), opponents=[(14, 2)])
    g.states[0].upgrades.add(PowerUp.PUNK)
    g.run()
    events = bot0_events(g)
    chain = next(i for i, e in enumerate(events) if e.action.type is ActionType.CHAIN)
    assert chain <= 15 and events[chain].target == (14, 2)  # straight there (11 steps + the buy)
    assert events[chain].result.success and g.states[0].invalid == 0
    assert_clean_repair(g, leak)  # back to work after the chase


def test_battle_royale_punk_chases_the_leader():
    # Bot 1 is nearer but has fewer points; bot 2 leads: chase bot 2
    g = graded_game({}, [(20, 21)], spawn=(2, 2), opponents=[(8, 2), (2, 12)])
    g.states[0].upgrades.add(PowerUp.PUNK)
    g.states[1].score, g.states[2].score = 5, 20
    while not g.states[0].chain_used and g.move < 40:
        g.step()
    assert g.states[2].chained_left > 0 and g.states[1].chained_left == 0


def test_battle_royale_retargets_when_the_leader_changes():
    g = graded_game({}, [(20, 21)], spawn=(12, 2), opponents=[(2, 2), (22, 2)])
    g.states[0].upgrades.add(PowerUp.PUNK)
    g.states[1].score, g.states[2].score = 5, 20
    for _ in range(3 * 3):  # three moves each: bot 0 heads for the leader (bot 2, on the right)
        g.step()
    assert g.states[0].pos[0] > 12
    g.states[1].score = 50  # bot 1 takes the lead: turn around
    while not g.states[0].chain_used and g.move < 40:
        g.step()
    assert g.states[1].chained_left > 0 and g.states[2].chained_left == 0


def test_gives_up_a_hopeless_chase():
    class RunnerBot(BaseBot):  # flees from bot 0, too fast for a chained punk
        def act(self, view):
            hunter = view.bot_positions()[0]
            away = [(abs(n[0] - hunter[0]) + abs(n[1] - hunter[1]), d) for d in Dir
                    if view.is_walkable(n := view.neighbor(d))]
            return Action.move(max(away)[1]) if away else Action.detect()

    leak = (5, 21)
    masks = [[H if y % 2 else 0 for _ in range(25)] for y in range(25)]
    g = Game([TenchijinBot, RunnerBot], FREE_SATELLITE, seed=0, masks=masks, leaks=[leak],
             spawns=[(2, 12), (2, 0)])
    for y, row in enumerate(g.cells):
        for x, cell in enumerate(row):
            if cell.has_pipe:
                cell.risk = "E" if (x, y) == leak else "A"
    g.states[0].upgrades.add(PowerUp.PUNK)
    g.states[0].chained_left = 500  # chained itself: half as fast as the runner
    g.run()
    chase_end = g.bots[0].chase_until
    first_dig = next(i for i, e in enumerate(bot0_events(g)) if e.action.type is ActionType.UNEARTH)
    assert chase_end is not None and chase_end < first_dig  # chased first, but only for a while
    assert_clean_repair(g, leak)  # then back to work


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
    assert unearthed_squares(g)[0] == leak
    assert_clean_repair(g, leak)
    assert (state.leaks_fixed, state.score, state.invalid) == (1, 55, 0)
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


@pytest.mark.parametrize("spawn_x", [3, 4, 5])
def test_end_of_round_reopens_and_refills_a_cut_short_repair(spawn_x):
    # 40-move round, far leak: the repair just fits when it starts. Chained after closing its first
    # valve, it no longer does: the bot drops it, reopens the valve and refills that hole. Only the
    # leak's own hole stays open (it cannot be refilled unfixed): 5 found - 2 = 3 points.
    leak = (20, 9)
    g = graded_game({leak: "E"}, [leak], spawn=(spawn_x, 8), config=replace(FREE_SATELLITE, max_moves=40))
    while not g.bots[0].my_closed:
        g.step()
    g.states[0].chained_left = 500
    g.run()
    state = g.states[0]
    kinds = [e.action.type for e in bot0_events(g)]
    assert ActionType.FIX not in kinds and ActionType.OPEN_VALVE in kinds
    assert (state.closed_valves, state.open_holes, state.score, state.invalid) == (0, 1, 3, 0)
    assert g.cell(leak).surface is Surface.UNEARTHED and g.cell(leak).pipe_state is PipeState.BROKEN


@pytest.mark.parametrize(("chained", "first"), [(0, (8, 3)), (100, (2, 3))])
def test_chained_travel_counts_double_in_the_scoring(chained, first):
    # E 6 steps away: 55 / (6 + 19) beats the adjacent D (~1.9 points per move) when free,
    # but not with 12 moves of travel and slower repair walking when chained
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


# --- Waterworks, new leaks, rechecking ------------------------------------------------------------
def test_walks_to_the_waterworks_to_buy():
    shop = (10, 9)
    config = replace(FREE_SATELLITE, cost_satellite=25)
    g = graded_game({(20, 9): "E"}, [(20, 9)], spawn=(2, 2), config=config, waterworks=shop)
    g.states[0].score = 25  # can afford it right away
    while PowerUp.SATELLITE not in g.states[0].upgrades:
        g.step()
    buy = bot0_events(g)[-1]
    assert buy.action.type is ActionType.BUY and buy.from_pos == shop
    assert len(bot0_events(g)) == 8 + 7 + 1  # shortest walk there, then the purchase
    assert g.states[0].invalid == 0


def test_never_digs_the_waterworks():
    shop = (6, 3)  # graded D, but buildings cannot be dug (nor leak, nor its pipe neighbours)
    grades = {shop: "D", (5, 3): "D", (2, 3): "D", (20, 9): "E"}
    g = graded_game(grades, [(20, 9)], spawn=(2, 2), waterworks=shop)
    g.run()
    assert shop not in unearthed_squares(g) and (5, 3) not in unearthed_squares(g)
    assert (2, 3) in unearthed_squares(g) and g.states[0].invalid == 0


B_SQUARES = [(4, 5), (12, 7), (20, 11), (6, 15), (16, 17), (10, 21)]


def recheck_game():
    """An E leak and six B squares; a new leak appears at move 30, images every 20 moves."""
    grades = {(22, 3): "E", **{p: "B" for p in B_SQUARES}}
    config = replace(FREE_SATELLITE, extra_leak_moves=(30,), satellite_refresh=20, max_moves=300)
    return graded_game(grades, [(22, 3)], spawn=(2, 2), config=config)


def test_after_its_last_leak_it_rechecks_b_squares_and_repairs_a_new_leak():
    g = recheck_game()
    while len(g.leak_positions) == 1:
        g.step()
    new_leak = g.leak_positions[-1]
    assert new_leak in B_SQUARES  # once a satellite is up, new leaks only hit B+ squares
    g.run()
    state = g.states[0]
    assert g.cell(new_leak).pipe_state is PipeState.FIXED and state.leaks_fixed == 2
    assert all(g.cell(p).revealed for p in B_SQUARES)  # every B square rechecked
    assert (state.open_holes, state.closed_valves, state.invalid) == (0, 0, 0)


def test_never_idles_while_b_squares_are_left_undug():
    g = recheck_game()
    while not g.over:
        undug = [p for p in B_SQUARES if not g.cell(p).revealed]
        event = g.step()
        if event.bot_id == 0 and event.action.type is ActionType.DETECT:
            assert not undug, f"detected at move {g.move} with {undug} undug"


def test_a_refresh_reveals_the_new_grade():
    g = recheck_game()
    while len(g.leak_positions) == 1:
        g.step()
    new_leak = g.leak_positions[-1]
    assert BotView(g, 0).risk(new_leak) == "B"  # stale image
    while BotView(g, 0).satellite_age != 1:
        g.step()
    assert BotView(g, 0).risk(new_leak) == g.cell(new_leak).risk
