"""Manual mode (headless, SDL dummy driver): a human plays one bot, one key press = one move."""

import os
import time

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pytest

pygame = pytest.importorskip("pygame")

from bots.random_bot import RandomBot  # noqa: E402
from leakbots.game import Game  # noqa: E402
from leakbots.gui import app as app_module  # noqa: E402
from leakbots.gui.manual import HumanBot, ManualControls  # noqa: E402
from leakbots.model import Action, ActionType, Dir, PipeState, PowerUp, Surface  # noqa: E402

SPAWN = (12, 12)  # centre of the default 25 x 25 grid
RIGHT = (13, 12)


@pytest.fixture
def app():
    a = app_module.App([RandomBot, RandomBot], seed=3, sim_rounds=5)
    yield a
    pygame.quit()


@pytest.fixture
def manual(app):
    app.toggle_manual()
    return app


def down(a, k):
    a.handle_event(pygame.event.Event(pygame.KEYDOWN, key=k))


def up(a, k):
    a.handle_event(pygame.event.Event(pygame.KEYUP, key=k))


def click(a, rect):
    for kind in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
        a.handle_event(pygame.event.Event(kind, button=1, pos=rect.center))


def hold(a, modifier, direction_key):
    """Hold E/R/F, tap a direction, release (like a player would)."""
    down(a, modifier)
    down(a, direction_key)
    up(a, modifier)


def use_map(a, pipe=None, leak=False):
    """Swap in a hand-made round: empty grid, human at SPAWN, optionally one pipe (and leak)."""
    cfg = a.config
    masks = [[0] * cfg.width for _ in range(cfg.height)]
    if pipe:
        masks[pipe[1]][pipe[0]] = Dir.LEFT.bit | Dir.RIGHT.bit
    a.game = Game([HumanBot], cfg, seed=1, masks=masks, leaks=[pipe] if leak else [], spawns=[SPAWN])
    a.renderer.set_game(a.game)
    return a.game


def last_action(a):
    return a.game.history[-1].action


# --- Switching ------------------------------------------------------------------------------------
def test_manual_button_switches_lineup_and_back(app):
    seed = app.seed
    click(app, app.manual_button.rect)
    assert app.manual and app.bot_classes == [HumanBot]
    assert app.game.n_bots == 1 and isinstance(app.game.bots[0], HumanBot)
    assert app.game.turn == 0 and app.seed == seed  # fresh round, same map
    assert app.labels == ["You"] and app._mode_label() == "MANUAL PLAY"
    app.set_language("ja")
    assert app.labels == ["あなた"]

    click(app, app.manual_button.rect)  # again: the bots the app was started with
    assert not app.manual and app.bot_classes == [RandomBot, RandomBot]
    assert app.game.n_bots == 2 and app.game.turn == 0
    assert app.labels == ["RandomBot #1", "RandomBot #2"]


def test_constructor_flag_starts_in_manual_mode():
    a = app_module.App([RandomBot], seed=5, manual=True)
    try:
        assert a.manual and a.bot_classes == [HumanBot] and a.base_bots == [RandomBot]
        down(a, pygame.K_q)
        assert a.game.turn == 1
        a.toggle_manual()
        assert a.bot_classes == [RandomBot] and not a.manual
    finally:
        pygame.quit()


def test_entering_manual_cancels_simulation(app):
    app.sim_rounds = 10_000
    app.toggle_simulation()
    assert app.simulating
    app.toggle_manual()
    assert not app.simulating and app.sim_result.cancelled


# --- Actions ---------------------------------------------------------------------------------------
def test_wasd_move_one_square_per_key(manual):
    game = use_map(manual)
    path = [(pygame.K_w, (12, 11)), (pygame.K_a, (11, 11)), (pygame.K_s, (11, 12)), (pygame.K_d, (12, 12))]
    for turn, (key, pos) in enumerate(path, start=1):
        down(manual, key)
        up(manual, key)
        assert game.turn == turn  # exactly one move per key press
        assert game.states[0].pos == pos and last_action(manual).type is ActionType.MOVE
    assert game.states[0].invalid == 0


def test_q_detects_all_four_sides(manual):
    game = use_map(manual, pipe=RIGHT)
    down(manual, pygame.K_q)
    res = game.states[0].last_result
    assert game.turn == 1 and res.success and last_action(manual) == Action.detect()
    assert dict(res.scan) == {(12, 11): False, (12, 13): False, (11, 12): False, RIGHT: True}


def test_hold_e_r_f_unearth_fix_refill_the_right_side(manual):
    game = use_map(manual, pipe=RIGHT, leak=True)
    cell = game.cell(RIGHT)

    hold(manual, pygame.K_e, pygame.K_d)
    assert game.turn == 1 and last_action(manual) == Action.unearth(Dir.RIGHT)
    assert cell.surface is Surface.UNEARTHED and game.states[0].pos == SPAWN
    assert game.states[0].score == game.config.points_leak_found

    down(manual, pygame.K_d)  # E released: D moves again, into the hole -> invalid, red toast
    assert game.turn == 2 and last_action(manual) == Action.move(Dir.RIGHT)
    assert game.states[0].invalid == 1 and "unearthed" in manual._toast[0]
    assert manual._toast[2] == app_module.theme.DANGER

    hold(manual, pygame.K_r, pygame.K_d)
    assert game.turn == 3 and cell.pipe_state is PipeState.FIXED

    hold(manual, pygame.K_f, pygame.K_d)
    assert game.turn == 4 and cell.surface is Surface.UNTOUCHED

    down(manual, pygame.K_d)
    assert game.turn == 5 and game.states[0].pos == RIGHT


def test_latest_held_modifier_wins_and_focus_loss_releases():
    controls = ManualControls()
    controls.key_down(pygame.K_e)
    controls.key_down(pygame.K_f)
    assert controls.key_down(pygame.K_w) == Action.refill(Dir.UP)
    controls.key_up(pygame.K_f)
    assert controls.key_down(pygame.K_w) == Action.unearth(Dir.UP)
    controls.reset()  # what WINDOWFOCUSLOST does
    assert controls.key_down(pygame.K_w) == Action.move(Dir.UP)


def test_focus_lost_event_clears_held_keys(manual):
    use_map(manual)
    down(manual, pygame.K_e)
    manual.handle_event(pygame.event.Event(pygame.WINDOWFOCUSLOST))
    down(manual, pygame.K_w)
    assert last_action(manual) == Action.move(Dir.UP)


def test_buy_keys_with_and_without_enough_points(manual):
    game = use_map(manual)
    st = game.states[0]
    down(manual, pygame.K_1)  # 0 points: invalid, but still exactly one move
    assert game.turn == 1 and not st.upgrades and st.invalid == 1
    assert str(game.config.cost_ut) in manual._toast[0]
    down(manual, pygame.K_2)
    assert game.turn == 2 and not st.upgrades

    st.score = 100
    down(manual, pygame.K_1)
    assert game.turn == 3 and st.upgrades == {PowerUp.UT}
    down(manual, pygame.K_KP2)  # keypad digits work too
    assert game.turn == 4 and st.upgrades == {PowerUp.UT, PowerUp.SATELLITE}
    assert st.score == 100 - game.config.cost_ut - game.config.cost_satellite


# --- Global controls in manual mode ----------------------------------------------------------------
def test_bot_hotkeys_do_not_fire(manual):
    game = use_map(manual)
    marks, speed = manual.show_marks, manual.speed
    down(manual, pygame.K_SPACE)
    assert not manual.playing and game.turn == 0
    down(manual, pygame.K_m)
    assert manual.sim is None
    down(manual, pygame.K_UP)
    assert manual.speed == speed
    down(manual, pygame.K_d)  # D = move right, not marks
    assert manual.show_marks is marks and last_action(manual) == Action.move(Dir.RIGHT)
    down(manual, pygame.K_r)  # R = fix modifier, not restart (no move on its own)
    assert manual.game is game and game.turn == 1
    up(manual, pygame.K_r)
    down(manual, pygame.K_s)  # S = move down, not a bot step
    assert game.turn == 2 and last_action(manual) == Action.move(Dir.DOWN)
    assert game.states[0].invalid == 0
    manual.update(1.0, 50.0)  # frames never step on their own
    assert game.turn == 2


def test_kept_hotkeys_still_work(manual):
    seed = manual.seed
    pipes = manual.ghost_pipes
    down(manual, pygame.K_p)
    assert manual.ghost_pipes is not pipes
    down(manual, pygame.K_l)
    assert manual.lang == "ja"
    down(manual, pygame.K_h)
    assert manual.show_rules
    down(manual, pygame.K_w)  # game keys are paused under the rulebook
    assert manual.game.turn == 0
    down(manual, pygame.K_ESCAPE)
    assert not manual.show_rules and manual.running
    down(manual, pygame.K_n)
    assert manual.manual and manual.game.n_bots == 1 and manual.seed != seed


def test_clock_buttons_disabled_and_restart_keeps_manual(manual):
    play, step, restart, new_map, sim = manual.buttons[:5]
    for button in (play, step, sim):
        click(manual, button.rect)
    assert not manual.playing and manual.game.turn == 0 and manual.sim is None
    speed, slider = manual.speed, manual.slider.rect
    knob_end = (slider.right - 2, slider.centery)
    manual.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=knob_end))
    assert manual.speed == speed  # the speed slider is off too
    down(manual, pygame.K_q)
    seed = manual.seed
    click(manual, restart.rect)
    assert manual.manual and manual.game.turn == 0 and manual.seed == seed
    assert isinstance(manual.game.bots[0], HumanBot)
    click(manual, new_map.rect)
    assert manual.manual and manual.game.n_bots == 1


def test_round_over_ignores_keys_and_shows_banner(manual):
    game = manual.game
    while not game.over:
        down(manual, pygame.K_q)
    turn = game.turn
    down(manual, pygame.K_w)
    assert game.turn == turn == game.config.max_moves
    manual.draw(time.perf_counter())  # game-over banner path with the human's final score


# --- Drawing ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("lang", ["en", "ja"])
def test_help_box_draws_in_both_languages(manual, lang, tmp_path):
    manual.set_language(lang)
    down(manual, pygame.K_d)
    down(manual, pygame.K_e)  # held modifier row is highlighted
    manual.draw(time.perf_counter())  # same clock as the move animations (like the frame loop)
    panel_bottom = manual.screen.get_height() - 58 - 8  # the help box ends 8 px above the hints
    assert manual.help_bottom <= panel_bottom
    pygame.image.save(manual.screen, str(tmp_path / f"manual_{lang}.png"))
