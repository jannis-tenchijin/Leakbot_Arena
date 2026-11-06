"""Rules and scoring on a hand-made 5x5 map.

Layout (x right, y down): a straight pipe along row y=2 that leads off both edges,
with a leak at (2, 2). Bot 0 spawns at (2, 1) directly above the leak.
"""

from dataclasses import replace

import pytest

from leakbots import Action, ActionType, BaseBot, Dir, Game, GameConfig, PipeState, PowerUp, Surface
from leakbots.bot_api import BotView

H = Dir.LEFT.bit | Dir.RIGHT.bit
MASKS = [[0] * 5, [0] * 5, [H] * 5, [0] * 5, [0] * 5]
LEAK = (2, 2)
CONFIG = GameConfig(width=5, height=5, max_moves=10, n_leaks=1)


class IdleBot(BaseBot):
    """Returns no action (always invalid), so tests drive the game via apply_action."""

    def act(self, view):
        return None


def make_game(n_bots=1, spawns=((2, 1), (0, 0)), config=CONFIG, bot=IdleBot):
    return Game([bot] * n_bots, config, seed=0, masks=MASKS, leaks=[LEAK], spawns=list(spawns[:n_bots]))


def ok(game, action, bot_id=0):
    result = game.apply_action(bot_id, action)
    assert result.success, result.message
    return result


def bad(game, action, bot_id=0):
    result = game.apply_action(bot_id, action)
    assert not result.success
    return result


# --- Movement --------------------------------------------------------------------------------
def test_move_updates_position_and_occupancy():
    g = make_game()
    ok(g, Action.move(Dir.LEFT))
    assert g.states[0].pos == (1, 1)
    assert g.cell((1, 1)).occupant == 0 and g.cell((2, 1)).occupant is None


def test_cannot_leave_grid():
    g = make_game(spawns=((0, 0),))
    assert "outside" in bad(g, Action.move(Dir.UP)).message
    assert g.states[0].pos == (0, 0)


def test_cannot_stand_on_unearthed_cell():
    g = make_game()
    ok(g, Action.unearth(Dir.DOWN))
    assert "unearthed" in bad(g, Action.move(Dir.DOWN)).message


def test_cannot_move_onto_other_bot():
    g = make_game(n_bots=2, spawns=((2, 1), (3, 1)))
    assert "occupied" in bad(g, Action.move(Dir.RIGHT)).message


# --- Detect ----------------------------------------------------------------------------------
def test_detect_scores_once_per_pipe_cell_and_is_private():
    g = make_game(n_bots=2)
    r = ok(g, Action.detect())  # around (2, 1): only (2, 2) below holds a pipe
    assert r.scan == {(2, 0): False, (2, 2): True, (1, 1): False, (3, 1): False}
    assert r.points == 1 and r.scan_leaks is None
    assert ok(g, Action.detect()).points == 0  # same squares again: no points
    assert g.states[0].score == 1 and g.states[0].pipes_detected == 1
    assert dict(BotView(g, 0).detected) == {(2, 1): False, **r.scan}  # spawn square + scan
    assert dict(BotView(g, 1).detected) == {(0, 0): False}  # other bot only knows its spawn


def test_detect_on_unearthed_pipe_scores_nothing():
    g = make_game()
    ok(g, Action.unearth(Dir.DOWN))
    assert ok(g, Action.detect()).points == 0


def test_pipe_point_never_twice_after_dig_and_refill():
    g = make_game(spawns=((1, 1),))
    assert ok(g, Action.detect()).points == 1  # (1, 2) pipe, first time
    ok(g, Action.unearth(Dir.DOWN))
    ok(g, Action.refill(Dir.DOWN))
    assert ok(g, Action.detect()).points == 0  # covered again, still discovered
    assert g.states[0].score == 1


def test_unearthed_pipe_counts_as_discovered_for_every_bot():
    g = make_game(n_bots=2, spawns=((1, 1), (1, 3)))
    ok(g, Action.unearth(Dir.UP), bot_id=1)  # bot 1 digs (1, 2) without detecting
    ok(g, Action.refill(Dir.UP), bot_id=1)
    assert ok(g, Action.detect(), bot_id=0).points == 0  # bot 0 saw (1, 2) dug open
    assert ok(g, Action.detect(), bot_id=1).points == 0
    ok(g, Action.move(Dir.RIGHT), bot_id=0)  # bot 0 -> (2, 1)
    assert ok(g, Action.detect(), bot_id=0).points == 1  # (2, 2) never dug: +1


def test_refilled_square_stays_public():
    g = make_game(spawns=((1, 1),))
    ok(g, Action.unearth(Dir.DOWN))
    ok(g, Action.refill(Dir.DOWN))
    info = BotView(g, 0).cell((1, 2))
    assert info.surface is Surface.UNTOUCHED and info.revealed
    assert info.has_pipe is True and info.pipe_state is PipeState.OK
    assert info.pipe_dirs == {Dir.LEFT, Dir.RIGHT} and info.walkable


# --- Power-ups -------------------------------------------------------------------------------
def give_points(g, bot_id, points):
    g.states[bot_id].score = points  # test shortcut: pretend the bot already earned these


def test_buy_needs_enough_points_and_uses_the_move():
    g = make_game()
    r = bad(g, Action.buy(PowerUp.UT))
    assert "costs 15" in r.message and g.states[0].upgrades == set()
    give_points(g, 0, 20)
    r = ok(g, Action.buy(PowerUp.UT))
    assert r.points == -15 and g.states[0].score == 5 and g.states[0].spent == 15
    assert BotView(g, 0).upgrades == {PowerUp.UT} and BotView(g, 0).has(PowerUp.UT)
    assert "already" in bad(g, Action.buy(PowerUp.UT)).message  # once per round
    assert "costs 25" in bad(g, Action.buy(PowerUp.SATELLITE)).message


def test_buy_is_public_and_needs_a_power_up():
    g = make_game(n_bots=2)
    give_points(g, 1, 30)
    ok(g, Action.buy(PowerUp.SATELLITE), bot_id=1)
    assert BotView(g, 0).upgrades_of(1) == {PowerUp.SATELLITE}
    assert BotView(g, 0).points["satellite_cost"] == 25
    assert not g.apply_action(0, Action(ActionType.BUY)).success  # no power-up named


def test_ut_reports_leaks_without_points():
    g = make_game()
    give_points(g, 0, 15)
    ok(g, Action.buy(PowerUp.UT))
    r = ok(g, Action.detect())  # around (2, 1), including the leak square below
    assert r.points == 1  # only the pipe discovery; seeing the leak pays nothing
    assert r.scan_leaks == {(2, 0): False, LEAK: True, (1, 1): False, (3, 1): False}
    assert dict(BotView(g, 0).detected_leaks) == r.scan_leaks  # spawn square was scanned before UT
    assert not g.cell(LEAK).leak_found  # unearthing it still pays the +5
    assert ok(g, Action.unearth(Dir.DOWN)).points == 5


# --- Unearth / fix / refill ----------------------------------------------------------------
def test_unearth_leak_scores_once_and_records_digger():
    g = make_game()
    r = ok(g, Action.unearth(Dir.DOWN))
    assert r.points == 5 and g.states[0].leaks_found == 1
    cell = g.cell(LEAK)
    assert cell.surface is Surface.UNEARTHED and cell.dug_by == 0
    assert "already" in bad(g, Action.unearth(Dir.DOWN)).message


def test_cannot_unearth_cell_with_bot():
    g = make_game(n_bots=2, spawns=((2, 1), (3, 1)))
    assert "standing" in bad(g, Action.unearth(Dir.RIGHT)).message


def isolate(g, leak):
    """Test shortcut: closed valves on every square the leak's pipe connects to."""
    for p in g.connected_neighbors(leak):
        g.cell(p).valve, g.cell(p).valve_open = True, False


def test_fix_requires_unearthed_broken_pipe():
    g = make_game()
    assert "unearth" in bad(g, Action.fix(Dir.DOWN)).message
    ok(g, Action.unearth(Dir.UP))  # (2, 0): no pipe
    assert "no pipe" in bad(g, Action.fix(Dir.UP)).message
    ok(g, Action.unearth(Dir.DOWN))
    assert "turn off the water" in bad(g, Action.fix(Dir.DOWN)).message
    isolate(g, LEAK)
    r = ok(g, Action.fix(Dir.DOWN))
    assert r.points == 40 and g.cell(LEAK).pipe_state is PipeState.FIXED
    assert "not broken" in bad(g, Action.fix(Dir.DOWN)).message
    assert g.states[0].score == 5 + 40


def test_refill_blocked_on_leak_until_fixed():
    g = make_game()
    ok(g, Action.unearth(Dir.DOWN))
    assert "fix" in bad(g, Action.refill(Dir.DOWN)).message
    isolate(g, LEAK)
    ok(g, Action.fix(Dir.DOWN))
    ok(g, Action.refill(Dir.DOWN))
    cell = g.cell(LEAK)
    assert cell.surface is Surface.UNTOUCHED and cell.dug_by is None
    ok(g, Action.move(Dir.DOWN))  # walkable again


def test_refill_any_hole_including_other_bots():
    g = make_game(n_bots=2, spawns=((2, 1), (2, 3)))
    ok(g, Action.unearth(Dir.LEFT), bot_id=1)  # bot 1 digs (1, 3), no pipe
    assert "nothing" in bad(g, Action.refill(Dir.LEFT)).message  # bot 0's left (1, 1) is untouched
    ok(g, Action.move(Dir.LEFT))  # bot 0 -> (1, 1)
    ok(g, Action.unearth(Dir.DOWN))  # (1, 2) pipe, dug by bot 0
    ok(g, Action.refill(Dir.DOWN))
    assert g.cell((1, 2)).surface is Surface.UNTOUCHED


# --- End of game -----------------------------------------------------------------------------
def test_open_pipe_holes_cost_the_latest_digger():
    cfg = replace(CONFIG, max_moves=1)
    g = make_game(n_bots=2, spawns=((1, 1), (3, 3)), config=cfg)
    ok(g, Action.unearth(Dir.DOWN), bot_id=0)  # (1, 2) pipe -> bot 0
    ok(g, Action.unearth(Dir.UP), bot_id=1)  # (3, 2) pipe -> bot 1
    ok(g, Action.refill(Dir.UP), bot_id=1)  # bot 1 refills its hole ...
    ok(g, Action.unearth(Dir.UP), bot_id=1)  # ... and digs it again: still bot 1's
    ok(g, Action.unearth(Dir.RIGHT), bot_id=1)  # (4, 3) no pipe: no penalty
    assert g.open_pipe_holes(0) == 1 and g.open_pipe_holes(1) == 1
    g.run()  # 1 move each: both IdleBots waste it, then penalties apply
    assert g.over
    assert (g.states[0].open_holes, g.states[0].score) == (1, -2)
    assert (g.states[1].open_holes, g.states[1].score) == (1, -2)


def test_refilled_hole_is_not_penalised():
    g = make_game(config=replace(CONFIG, max_moves=1))
    ok(g, Action.unearth(Dir.DOWN))
    isolate(g, LEAK)
    ok(g, Action.fix(Dir.DOWN))
    ok(g, Action.refill(Dir.DOWN))
    g.run()
    assert g.states[0].score == 5 + 40 and g.states[0].open_holes == 0


# --- Turn loop -------------------------------------------------------------------------------
class RecorderBot(BaseBot):
    calls: list = []

    def act(self, view):
        RecorderBot.calls.append((view.bot_id, view.move))
        return Action.detect(Dir.UP)


def test_turn_order_and_move_cap():
    RecorderBot.calls = []
    g = make_game(n_bots=2, bot=RecorderBot, config=replace(CONFIG, max_moves=3))
    g.run()
    assert RecorderBot.calls == [(0, 0), (1, 0), (0, 1), (1, 1), (0, 2), (1, 2)]
    assert g.over and g.turn == 6 and g.step() is None


def test_single_player_acts_every_turn():
    RecorderBot.calls = []
    make_game(n_bots=1, bot=RecorderBot, config=replace(CONFIG, max_moves=4)).run()
    assert [c[0] for c in RecorderBot.calls] == [0, 0, 0, 0]


class CrashBot(BaseBot):
    def act(self, view):
        raise RuntimeError("boom")


class JunkBot(BaseBot):
    def act(self, view):
        return "up"


@pytest.mark.parametrize("bot", [CrashBot, JunkBot])
def test_broken_bots_only_waste_their_turn(bot):
    g = make_game(bot=bot, config=replace(CONFIG, max_moves=5)).run()
    assert g.over and g.states[0].invalid == 5 and g.states[0].score == 0


# --- What bots can see -----------------------------------------------------------------------
def test_view_hides_buried_pipes_and_shows_dug_ones():
    g = make_game()
    view = BotView(g, 0)
    assert view.cell(LEAK).has_pipe is None and view.cell(LEAK).pipe_dirs == frozenset()
    ok(g, Action.unearth(Dir.DOWN))
    info = view.cell(LEAK)
    assert info.has_pipe is True and info.pipe_state is PipeState.BROKEN
    assert info.pipe_dirs == {Dir.LEFT, Dir.RIGHT}
    assert not hasattr(info, "dug_by")
    assert view.me == (2, 1) and view.cell((2, 1)).bot == 0 and not view.is_walkable(LEAK)


def test_same_seed_same_game():
    from bots.random_bot import RandomBot

    a = Game([RandomBot, RandomBot], seed=42).run()
    b = Game([RandomBot, RandomBot], seed=42).run()
    assert [s.score for s in a.states] == [s.score for s in b.states]
    assert [e.action for e in a.history] == [e.action for e in b.history]
