"""4-way detect, free auto-detect (spawn + every step) and UT leak readings.

Same 5x5 layout as tests/test_rules.py (x right, y down): a straight pipe along row y=2 that
leads off both edges, with a leak at (2, 2). Bot 0 spawns at (2, 1) directly above the leak.
"""

import os

import pytest

from leakbots import Action, ActionType, BaseBot, Dir, Game, GameConfig, PowerUp
from leakbots.bot_api import BotView

H = Dir.LEFT.bit | Dir.RIGHT.bit
MASKS = [[0] * 5, [0] * 5, [H] * 5, [0] * 5, [0] * 5]
LEAK = (2, 2)
CONFIG = GameConfig(width=5, height=5, max_moves=10, n_leaks=1)
AROUND_SPAWN = {(2, 0): False, LEAK: True, (1, 1): False, (3, 1): False}  # 4-way scan from (2, 1)


class IdleBot(BaseBot):
    """Returns no action (always invalid), so tests drive the game via apply_action."""

    def act(self, view):
        return None


def make_game(n_bots=1, spawns=((2, 1), (0, 0)), bot=IdleBot):
    return Game([bot] * n_bots, CONFIG, seed=0, masks=MASKS, leaks=[LEAK], spawns=list(spawns[:n_bots]))


def ok(game, action, bot_id=0):
    result = game.apply_action(bot_id, action)
    assert result.success, result.message
    return result


def buy_ut(game, bot_id=0):
    game.states[bot_id].score += game.config.cost_ut  # test shortcut: pretend the bot earned the price
    ok(game, Action.buy(PowerUp.UT), bot_id)


# --- 4-way detect ------------------------------------------------------------------------------
def test_detect_scans_all_four_neighbours():
    g = make_game()
    r = ok(g, Action.detect())
    assert r.scan == AROUND_SPAWN and r.points == 1 and r.scan_leaks is None
    s = g.states[0]
    assert s.detected == {(2, 1): False, **AROUND_SPAWN}  # spawn square + the four neighbours
    assert s.pipes_detected == 1 and s.score == 1


@pytest.mark.parametrize(
    "spawn, expected",
    [
        ((0, 0), {(1, 0): False, (0, 1): False}),  # corner
        ((4, 4), {(3, 4): False, (4, 3): False}),  # corner
        ((2, 0), {(2, 1): False, (1, 0): False, (3, 0): False}),  # top edge
        ((4, 1), {(4, 0): False, (4, 2): True, (3, 1): False}),  # right edge, pipe below
    ],
)
def test_detect_on_edges_and_corners_scans_only_inside_squares(spawn, expected):
    g = make_game(spawns=(spawn,))
    r = ok(g, Action.detect())  # always valid, even in a corner
    assert r.scan == expected
    assert r.points == sum(expected.values())


def test_detect_with_a_direction_is_accepted_and_ignored():
    g = make_game(spawns=((0, 0),))
    r = ok(g, Action(ActionType.DETECT, Dir.UP))  # UP would leave the grid: ignored
    assert r.scan == {(1, 0): False, (0, 1): False}
    assert g.states[0].facing is Dir.RIGHT  # detect does not turn the bot


class DirectionalDetectBot(BaseBot):
    def act(self, view):
        return Action(ActionType.DETECT, Dir.UP)  # like RandomBot's generic Action(kind, dir)


def test_detect_event_has_no_target_square():
    g = make_game(bot=DirectionalDetectBot)
    event = g.step()
    assert event.result.success and event.target is None
    assert event.result.scan == AROUND_SPAWN and event.result.points == 1


def test_detect_results_are_private():
    g = make_game(n_bots=2, spawns=((2, 1), (2, 3)))
    ok(g, Action.detect(), bot_id=0)
    assert dict(BotView(g, 1).detected) == {(2, 3): False}  # only its own spawn square
    r = ok(g, Action.detect(), bot_id=1)  # bot 1 scans the same leak square
    assert r.scan[LEAK] is True and r.points == 1  # +1 per square per bot, not per square
    assert dict(BotView(g, 0).detected) == {(2, 1): False, **AROUND_SPAWN}  # unchanged


def test_pipe_square_pays_once_per_bot():
    g = make_game()
    assert ok(g, Action.detect()).points == 1
    assert ok(g, Action.detect()).points == 0  # same squares again
    r = ok(g, Action.move(Dir.DOWN))  # onto the leak square: already discovered
    assert r.scan == {LEAK: True} and r.points == 0
    r = ok(g, Action.detect())  # from (2, 2): new pipe squares left and right
    assert r.scan == {(2, 1): False, (2, 3): False, (1, 2): True, (3, 2): True} and r.points == 2
    s = g.states[0]
    assert s.score == 3 and s.pipes_detected == 3 and s.discovered == {(1, 2), LEAK, (3, 2)}


def test_dug_and_refilled_squares_pay_nothing():
    g = make_game(spawns=((1, 1),))
    ok(g, Action.unearth(Dir.DOWN))  # (1, 2): intact pipe, now public
    r = ok(g, Action.detect())
    assert r.scan[(1, 2)] is True and r.points == 0  # recorded, but unearthed squares never pay
    ok(g, Action.refill(Dir.DOWN))
    assert ok(g, Action.detect()).points == 0  # refilled: still known to everyone
    r = ok(g, Action.move(Dir.DOWN))  # auto-detect of the refilled pipe square
    assert r.scan == {(1, 2): True} and r.points == 0
    assert g.states[0].score == 0 and g.states[0].pipes_detected == 0


# --- Auto-detect ---------------------------------------------------------------------------
class SpawnRecorder(BaseBot):
    seen: list = []

    def on_game_start(self, view):
        SpawnRecorder.seen.append((view.bot_id, dict(view.detected), view.score))

    def act(self, view):
        return None


def test_spawn_square_is_auto_detected_before_on_game_start():
    SpawnRecorder.seen = []
    g = make_game(n_bots=2, spawns=((0, 2), (4, 4)), bot=SpawnRecorder)
    assert SpawnRecorder.seen == [(0, {(0, 2): True}, 1), (1, {(4, 4): False}, 0)]
    assert g.states[0].pipes_detected == 1 and g.states[0].discovered == {(0, 2)}
    assert g.states[1].score == 0 and g.turn == 0


def test_move_auto_detects_the_destination():
    g = make_game(spawns=((1, 1),))
    r = ok(g, Action.move(Dir.RIGHT))  # (2, 1): no pipe
    assert r.scan == {(2, 1): False} and r.points == 0 and r.scan_leaks is None
    r = ok(g, Action.move(Dir.DOWN))  # (2, 2): new pipe square
    assert r.scan == {LEAK: True} and r.points == 1
    s = g.states[0]
    assert s.score == 1 and s.pipes_detected == 1 and s.detected[LEAK] is True


def test_failed_moves_and_other_actions_do_not_auto_detect():
    g = make_game(n_bots=2, spawns=((1, 1), (1, 2)))
    before = dict(g.states[0].detected)
    r = g.apply_action(0, Action.move(Dir.DOWN))  # (1, 2) is occupied by bot 1
    assert not r.success and r.scan is None and r.points == 0
    ok(g, Action.move(Dir.UP))  # (1, 0): fine, auto-detected
    r = g.apply_action(0, Action.move(Dir.UP))  # off the grid
    assert not r.success and r.scan is None
    r = ok(g, Action.unearth(Dir.RIGHT))  # (2, 0): digging does not detect
    assert r.scan is None and (2, 0) not in g.states[0].detected
    assert g.states[0].detected == {**before, (1, 0): False}


# --- UT ------------------------------------------------------------------------------------
def test_ut_reports_leaks_without_points_or_leak_found():
    g = make_game(n_bots=2)
    buy_ut(g)
    r = ok(g, Action.detect())
    assert r.scan_leaks == {(2, 0): False, LEAK: True, (1, 1): False, (3, 1): False}
    assert r.points == 1  # the pipe discovery only; seeing the leak pays nothing
    assert dict(BotView(g, 0).detected_leaks) == r.scan_leaks
    assert dict(BotView(g, 1).detected_leaks) == {}  # private
    assert g.cell(LEAK).leak_found is False and g.states[0].leaks_found == 0
    assert ok(g, Action.unearth(Dir.DOWN)).points == g.config.points_leak_found  # still +5


def test_ut_only_reads_squares_scanned_after_buying():
    g = make_game()
    assert ok(g, Action.detect()).scan_leaks is None  # before UT: no leak readings
    buy_ut(g)
    assert dict(BotView(g, 0).detected_leaks) == {}  # old results are not updated
    r = ok(g, Action.detect())  # scanning the same squares again reads them
    assert r.scan_leaks[LEAK] is True and r.points == 0
    assert (2, 1) not in g.states[0].detected_leaks  # spawn square was not scanned again


def test_ut_rescan_updates_an_old_reading():
    g = make_game()
    buy_ut(g)
    ok(g, Action.detect())
    assert g.states[0].detected_leaks[LEAK] is True
    ok(g, Action.unearth(Dir.DOWN))
    for p in g.connected_neighbors(LEAK):  # test shortcut: valves closed around the leak
        g.cell(p).valve, g.cell(p).valve_open = True, False
    ok(g, Action.fix(Dir.DOWN))
    ok(g, Action.refill(Dir.DOWN))
    assert g.states[0].detected_leaks[LEAK] is True  # stale until scanned again
    assert ok(g, Action.detect()).scan_leaks[LEAK] is False
    assert g.states[0].detected_leaks[LEAK] is False


def test_ut_auto_detect_reports_leaks():
    g = make_game()
    buy_ut(g)
    r = ok(g, Action.move(Dir.DOWN))  # step onto the leak square
    assert r.scan == {LEAK: True} and r.scan_leaks == {LEAK: True} and r.points == 1
    assert g.states[0].detected_leaks == {LEAK: True} and not g.cell(LEAK).leak_found
    assert ok(g, Action.move(Dir.UP)).scan_leaks == {(2, 1): False}
    assert ok(g, Action.unearth(Dir.DOWN)).points == g.config.points_leak_found


# --- Viewer --------------------------------------------------------------------------------
def test_gui_pings_every_scanned_square_and_draws_marks_under_bots():
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    pygame = pytest.importorskip("pygame")
    from leakbots.gui import theme
    from leakbots.gui.render import BoardRenderer

    pygame.init()
    theme.reset_fonts()
    try:
        g = make_game(bot=DirectionalDetectBot)
        renderer = BoardRenderer((0, 0), 24)
        renderer.set_game(g)
        renderer.on_turn(g.step(), now=0.0, move_time=0.1)
        pings = {e.pos: e.color for e in renderer.effects if e.kind == "ping"}
        bot_color = theme.BOT_COLORS[0]
        assert pings == {p: bot_color if pipe else theme.MUTED for p, pipe in AROUND_SPAWN.items()}
        assert [e.pos for e in renderer.effects if e.kind == "points"] == [(2, 1)]  # over the bot
        surf = pygame.Surface(renderer.board_rect().inflate(8, 8).size)
        renderer.draw(surf, 0.2, ghost_pipes=False, show_marks=True, sprites=[None])  # spawn mark under bot
    finally:
        pygame.quit()
        theme.reset_fonts()
