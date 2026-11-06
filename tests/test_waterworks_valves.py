"""Waterworks (power-ups sold there), leaks appearing mid-round, satellite refresh, valve repairs."""

from dataclasses import replace

import pytest

from leakbots import Action, BaseBot, Dir, Game, GameConfig, PipeState, PowerUp, Surface
from leakbots.bot_api import BotView
from leakbots.simulation import SimulationRunner

H = Dir.LEFT.bit | Dir.RIGHT.bit
MASKS = [[0] * 5, [0] * 5, [H] * 5, [0] * 5, [0] * 5]  # straight pipe along y=2, open at both edges
LEAK = (2, 2)
CONFIG = GameConfig(width=5, height=5, max_moves=50, n_leaks=1, punk_spawn_move=0, extra_leak_moves=())


class IdleBot(BaseBot):
    def act(self, view):
        return None


def make_game(n_bots=1, spawns=((2, 1), (2, 3)), config=CONFIG, waterworks="auto"):
    return Game([IdleBot] * n_bots, config, seed=0, masks=MASKS, leaks=[LEAK], spawns=list(spawns[:n_bots]),
                waterworks=waterworks)


def ok(g, action, bot_id=0):
    r = g.apply_action(bot_id, action)
    assert r.success, r.message
    return r


def bad(g, action, bot_id=0):
    r = g.apply_action(bot_id, action)
    assert not r.success
    return r


# --- Waterworks ------------------------------------------------------------------------------------
@pytest.mark.parametrize("seed", range(20))
def test_waterworks_sits_on_the_pipeline_away_from_leaks(seed):
    g = Game([IdleBot], GameConfig(), seed=seed)
    pos = g.waterworks_pos
    assert pos is not None and g.cell(pos).has_pipe and g.cell(pos).building
    near = {pos, *g.connected_neighbors(pos)}
    assert not near & set(g.leak_positions)
    assert BotView(g, 0).waterworks_pos == pos and BotView(g, 0).cell(pos).building


def test_power_ups_are_only_sold_at_the_waterworks():
    g = make_game(waterworks=(0, 2), spawns=((0, 1),))
    g.states[0].score = 40
    assert "waterworks" in bad(g, Action.buy(PowerUp.UT)).message
    ok(g, Action.move(Dir.DOWN))  # onto the waterworks: buildings are walkable
    ok(g, Action.buy(PowerUp.UT))


def test_waterworks_cannot_be_dug_and_punk_avoids_it():
    g = make_game(waterworks=(1, 2), spawns=((1, 1),))
    assert "waterworks" in bad(g, Action.unearth(Dir.DOWN)).message
    g2 = Game([IdleBot], GameConfig(punk_spawn_move=1), seed=3)
    g2.step()
    assert g2.punk_pos != g2.waterworks_pos and g2.cell(g2.punk_pos).surface is Surface.UNTOUCHED


def test_hand_made_maps_have_no_waterworks_unless_given():
    g = make_game()
    assert g.waterworks_pos is None
    g.states[0].score = 20
    ok(g, Action.buy(PowerUp.UT))  # no waterworks: buy anywhere


# --- New leaks during the round --------------------------------------------------------------------
def test_new_leaks_appear_at_200_300_400():
    g = Game([IdleBot], GameConfig(), seed=4)
    start = len(g.leak_positions)
    while g.move < 199:
        g.step()
    assert len(g.leak_positions) == start
    g.step()  # move 200 begins
    assert len(g.leak_positions) == start + 1
    g.run()
    assert len(g.leak_positions) == start + 3
    for pos in g.leak_positions[start:]:
        assert g.cell(pos).pipe_state is PipeState.BROKEN and pos not in g._blocked_for_leaks()


def test_new_leak_spawns_on_a_buried_untouched_pipe():
    g = Game([IdleBot], replace(GameConfig(), extra_leak_moves=(2,)), seed=9)
    g.step(), g.step()
    pos = g.leak_positions[-1]
    cell = g.cell(pos)
    assert cell.has_pipe and cell.surface is Surface.UNTOUCHED and not cell.revealed


def test_with_a_satellite_new_leaks_avoid_grade_a_and_show_up_on_refresh():
    cfg = replace(GameConfig(), extra_leak_moves=(3,), satellite_refresh=5)
    g = Game([IdleBot], cfg, seed=2)
    g.states[0].score = 30
    g.cell(g.waterworks_pos).occupant = None
    g.cell(g.states[0].pos).occupant = None
    g.states[0].pos = g.waterworks_pos
    g.cell(g.waterworks_pos).occupant = 0
    ok(g, Action.buy(PowerUp.SATELLITE))
    a_squares = {p for p, r in BotView(g, 0).satellite.items() if r.risk == "A"}
    for _ in range(3):
        g.step()  # the leak appears at the start of move 3
    leak = g.leak_positions[-1]
    old_grade = BotView(g, 0).risk(leak)
    assert leak not in a_squares and old_grade != "A"
    assert g.cell(leak).risk in "BCDE"  # true grade redrawn as a leak's
    while g.move < 5:
        g.step()
    assert BotView(g, 0).risk(leak) == old_grade  # snapshot: not refreshed yet
    g.step()  # 5 own moves after the purchase: refreshed
    assert BotView(g, 0).risk(leak) == g.cell(leak).risk and BotView(g, 0).satellite_age == 1  # taken at move start


# --- Valves and the new fix rule -------------------------------------------------------------------
def dig_and_close(g, bot_id, direction):
    ok(g, Action.unearth(direction), bot_id)
    ok(g, Action.install_valve(direction), bot_id)
    ok(g, Action.close_valve(direction), bot_id)


def test_full_repair_sequence_scores_40_plus_5_per_valve():
    # bot 0 on the left neighbour's top, walks along y=1 doing the work
    g = make_game(spawns=((1, 1),), config=replace(CONFIG, max_moves=100))
    dig_and_close(g, 0, Dir.DOWN)  # (1, 2)
    ok(g, Action.move(Dir.RIGHT))
    ok(g, Action.move(Dir.RIGHT))  # now at (3, 1)
    dig_and_close(g, 0, Dir.DOWN)  # (3, 2)
    ok(g, Action.move(Dir.LEFT))  # (2, 1), above the leak
    assert ok(g, Action.unearth(Dir.DOWN)).points == 5
    assert ok(g, Action.fix(Dir.DOWN)).points == 40
    ok(g, Action.move(Dir.LEFT))
    assert ok(g, Action.open_valve(Dir.DOWN)).points == 5
    assert ok(g, Action.close_valve(Dir.DOWN)).points == 0
    assert ok(g, Action.open_valve(Dir.DOWN)).points == 0  # +5 only once per fix
    st = g.states[0]
    assert st.leaks_fixed == 1 and st.valves_reopened == 1


def test_fix_needs_every_connected_neighbour_closed():
    g = make_game(spawns=((1, 1),))
    dig_and_close(g, 0, Dir.DOWN)
    ok(g, Action.move(Dir.RIGHT))
    ok(g, Action.unearth(Dir.DOWN))
    r = bad(g, Action.fix(Dir.DOWN))
    assert "(3, 2)" in r.message  # the right-hand neighbour is still open


def test_valve_rules():
    g = make_game(spawns=((1, 1),))
    assert "unearthed pipe" in bad(g, Action.install_valve(Dir.DOWN)).message
    ok(g, Action.unearth(Dir.DOWN))
    assert "no valve" in bad(g, Action.close_valve(Dir.DOWN)).message
    ok(g, Action.install_valve(Dir.DOWN))
    assert "already a valve" in bad(g, Action.install_valve(Dir.DOWN)).message
    assert "already open" in bad(g, Action.open_valve(Dir.DOWN)).message
    ok(g, Action.close_valve(Dir.DOWN))
    assert BotView(g, 0).cell((1, 2)).valve is False
    ok(g, Action.unearth(Dir.UP))  # (1, 0): no pipe
    assert "unearthed pipe" in bad(g, Action.install_valve(Dir.UP)).message


def test_opponents_can_steal_the_fix_and_the_reopen_points():
    g = make_game(n_bots=2, spawns=((1, 1), (2, 3)))
    g.cell((1, 2)).surface = g.cell((3, 2)).surface = Surface.UNEARTHED
    for p in ((1, 2), (3, 2)):
        g.cell(p).valve, g.cell(p).valve_open, g.cell(p).valve_closer = True, False, 0  # bot 0's work
    g.cell(LEAK).surface = Surface.UNEARTHED
    assert ok(g, Action.fix(Dir.UP), bot_id=1).points == 40  # bot 1 steals the fix
    ok(g, Action.move(Dir.LEFT), bot_id=1)  # (1, 3)
    assert ok(g, Action.open_valve(Dir.UP), bot_id=1).points == 5  # and a reopen bonus


def test_closed_valves_cost_whoever_closed_them_even_if_buried():
    g = make_game(spawns=((1, 1),), config=replace(CONFIG, max_moves=1))
    dig_and_close(g, 0, Dir.DOWN)
    ok(g, Action.refill(Dir.DOWN))  # allowed: buries the closed valve
    g.run()
    st = g.states[0]
    assert st.closed_valves == 1 and st.open_holes == 0 and st.score == -10


# --- Simulation ------------------------------------------------------------------------------------
def test_every_simulated_round_uses_a_different_map():
    runner = SimulationRunner([IdleBot], n_rounds=5, seed=100, config=GameConfig(max_moves=1))
    seeds = [runner.seed + i for i in range(5)]
    maps = {tuple(Game([IdleBot], GameConfig(), seed=s).leak_positions) for s in seeds}
    assert len(set(seeds)) == 5 and len(maps) == 5
