"""Punk upgrade: pickup at move N, +points + mohawk, one ball and chain, drag-then-step."""

from dataclasses import replace

import pytest

from leakbots import Action, ActionType, BaseBot, Dir, Game, GameConfig, PowerUp, Surface
from leakbots.bot_api import BotView
from leakbots.model import PUNK_ITEM

H = Dir.LEFT.bit | Dir.RIGHT.bit
MASKS = [[0] * 5, [0] * 5, [H] * 5, [0] * 5, [0] * 5]  # one straight pipe along y=2
LEAK = (2, 2)
CONFIG = GameConfig(width=5, height=5, max_moves=50, n_leaks=1, punk_spawn_move=0, chain_duration=3)


class IdleBot(BaseBot):
    def act(self, view):
        return None


def make_game(n_bots=1, spawns=((0, 0), (1, 0)), config=CONFIG):
    return Game([IdleBot] * n_bots, config, seed=0, masks=MASKS, leaks=[LEAK], spawns=list(spawns[:n_bots]))


def ok(game, action, bot_id=0):
    result = game.apply_action(bot_id, action)
    assert result.success, result.message
    return result


def bad(game, action, bot_id=0):
    result = game.apply_action(bot_id, action)
    assert not result.success
    return result


def drop_punk(game, pos):
    game.cell(pos).item = PUNK_ITEM
    game.punk_pos = pos


def make_punk(game, bot_id=0):
    game.states[bot_id].upgrades.add(PowerUp.PUNK)


# --- Spawning --------------------------------------------------------------------------------------
def test_pickup_appears_at_the_start_of_the_configured_move():
    g = make_game(config=replace(CONFIG, punk_spawn_move=3))
    g.step()
    g.step()
    assert g.punk_pos is None  # two moves made, move 3 not started yet
    g.step()  # move 3 starts: the pickup appears before the bot acts
    x, y = g.punk_pos
    cell = g.cell((x, y))
    assert cell.item == PUNK_ITEM and cell.surface is Surface.UNTOUCHED and cell.occupant is None


def test_spawn_is_reproducible_and_can_be_disabled():
    cfg = replace(CONFIG, punk_spawn_move=2)
    a, b = make_game(config=cfg), make_game(config=cfg)
    for g in (a, b):
        g.step(), g.step()
    assert a.punk_pos == b.punk_pos is not None
    g = make_game()  # punk_spawn_move = 0
    for _ in range(10):
        g.step()
    assert g.punk_pos is None


def test_spawn_does_not_change_the_map_or_bot_rngs():
    with_punk = Game([IdleBot, IdleBot], GameConfig(), seed=11)
    without = Game([IdleBot, IdleBot], GameConfig(punk_spawn_move=0), seed=11)
    assert [s.pos for s in with_punk.states] == [s.pos for s in without.states]
    assert with_punk.leak_positions == without.leak_positions


# --- Collecting ------------------------------------------------------------------------------------
@pytest.mark.parametrize("n_bots", [1, 2])
def test_stepping_on_the_pickup_collects_it(n_bots):
    g = make_game(n_bots=n_bots, spawns=((0, 1), (4, 4)))
    drop_punk(g, (1, 1))
    r = ok(g, Action.move(Dir.RIGHT))
    st = g.states[0]
    assert PowerUp.PUNK in st.upgrades and st.bonus == 10 and r.points == 10
    assert g.cell((1, 1)).item is None and g.punk_pos is None
    view = BotView(g, 0)
    assert view.has(PowerUp.PUNK) and view.can_chain == (True)
    assert PowerUp.PUNK in BotView(g, n_bots - 1).upgrades_of(0)  # public


def test_pickup_is_public_and_blocks_digging():
    g = make_game(spawns=((1, 1),))
    drop_punk(g, (1, 2))
    view = BotView(g, 0)
    assert view.punk_pos == (1, 2) and view.cell((1, 2)).item == PUNK_ITEM
    assert "object" in bad(g, Action.unearth(Dir.DOWN)).message


def test_punk_cannot_be_bought():
    g = make_game()
    g.states[0].score = 100
    assert "cannot be bought" in bad(g, Action.buy(PowerUp.PUNK)).message


# --- Ball and chain --------------------------------------------------------------------------------
def test_chain_needs_punk_and_an_adjacent_opponent():
    g = make_game(n_bots=2, spawns=((0, 0), (1, 0)))
    assert "punk" in bad(g, Action.chain(Dir.RIGHT)).message
    make_punk(g)
    assert "no opponent" in bad(g, Action.chain(Dir.DOWN)).message
    assert "outside" in bad(g, Action.chain(Dir.UP)).message
    ok(g, Action.chain(Dir.RIGHT))
    assert g.states[1].chained_left == CONFIG.chain_duration
    assert BotView(g, 0).chained_moves(1) == CONFIG.chain_duration and not BotView(g, 0).can_chain


def test_only_one_chain_per_round():
    g = make_game(n_bots=2, spawns=((0, 0), (1, 0)))
    make_punk(g)
    ok(g, Action.chain(Dir.RIGHT))
    g.states[1].chained_left = 0  # even after it wore off
    assert "already used" in bad(g, Action.chain(Dir.RIGHT)).message


def test_single_player_punk_has_no_one_to_chain():
    g = make_game(spawns=((0, 0),))
    make_punk(g)
    assert "no opponent" in bad(g, Action.chain(Dir.RIGHT)).message


def test_chained_bot_needs_two_moves_per_step():
    g = make_game(n_bots=2, spawns=((1, 0), (0, 0)))
    g.states[0].chained_left = 10
    r = ok(g, Action.move(Dir.RIGHT))
    assert g.states[0].pos == (1, 0) and "dragging" in r.message  # first move only drags
    assert BotView(g, 0).dragging is Dir.RIGHT
    ok(g, Action.move(Dir.RIGHT))
    assert g.states[0].pos == (2, 0) and BotView(g, 0).dragging is None  # second move steps


def test_drag_resets_on_other_actions_or_directions():
    g = make_game(n_bots=2, spawns=((1, 1), (4, 4)))
    g.states[0].chained_left = 10
    ok(g, Action.move(Dir.RIGHT))
    ok(g, Action.detect())  # anything else drops the drag
    ok(g, Action.move(Dir.RIGHT))
    assert g.states[0].pos == (1, 1)
    ok(g, Action.move(Dir.UP))  # changing direction starts a new drag
    assert g.states[0].pos == (1, 1) and g.states[0].dragging is Dir.UP
    ok(g, Action.move(Dir.UP))
    assert g.states[0].pos == (1, 0)


def test_blocked_chained_move_is_invalid_without_dragging():
    g = make_game(n_bots=2, spawns=((0, 0), (1, 0)))
    g.states[0].chained_left = 10
    assert "occupied" in bad(g, Action.move(Dir.RIGHT)).message
    assert g.states[0].dragging is None


class StepRightBot(BaseBot):
    def act(self, view):
        return Action.move(Dir.RIGHT)


def test_chain_wears_off_after_chain_duration_moves():
    g = Game([StepRightBot], CONFIG, seed=0, masks=MASKS, leaks=[LEAK], spawns=[(0, 0)])
    g.states[0].chained_left = CONFIG.chain_duration  # 3 moves chained
    for _ in range(3):
        g.step()
    assert g.states[0].pos == (1, 0) and g.states[0].chained_left == 0  # drag, step, drag
    g.step()
    assert g.states[0].pos == (2, 0)  # free again: one move per step
    g.step()
    assert g.states[0].pos == (3, 0)


def test_chain_action_has_a_target_in_the_move_event():
    class ChainBot(BaseBot):
        def act(self, view):
            return Action.chain(Dir.RIGHT)

    g = Game([ChainBot, IdleBot], CONFIG, seed=0, masks=MASKS, leaks=[LEAK], spawns=[(0, 0), (1, 0)])
    make_punk(g)
    event = g.step()
    assert event.result.success and event.action.type is ActionType.CHAIN and event.target == (1, 0)
