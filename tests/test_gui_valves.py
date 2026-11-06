"""Viewer side of the waterworks / new leaks / valve rules (headless, SDL dummy driver)."""

import os
import time

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pytest

pygame = pytest.importorskip("pygame")

from bots.random_bot import RandomBot  # noqa: E402
from leakbots.config import GameConfig  # noqa: E402
from leakbots.game import Game  # noqa: E402
from leakbots.gui import app as app_module  # noqa: E402
from leakbots.gui import theme  # noqa: E402
from leakbots.gui.i18n import rulebook  # noqa: E402
from leakbots.gui.manual import HELP_ROWS, MODIFIER_KEYS, HumanBot, ManualControls  # noqa: E402
from leakbots.model import Action, ActionType, Dir, PipeState, PowerUp, Surface  # noqa: E402

SPAWN = (12, 12)  # centre of the default 25 x 25 grid
RIGHT = (13, 12)
H = Dir.LEFT.bit | Dir.RIGHT.bit


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


def hold(a, modifier, direction_key):
    down(a, modifier)
    down(a, direction_key)
    up(a, modifier)


def pipe_row_map(a, waterworks=None, leak=None, bots=(HumanBot,)):
    """Hand-made round: one straight pipe along row y=12, the (first) bot standing on it at SPAWN."""
    cfg = a.config
    masks = [[0] * cfg.width for _ in range(cfg.height)]
    masks[12] = [H] * cfg.width
    g = Game(list(bots), cfg, seed=1, masks=masks, leaks=[leak] if leak else [], spawns=[SPAWN],
             waterworks=waterworks)
    a.game = g
    a.renderer.set_game(g)
    return g


def colors_in(surface, rect):
    """Set of RGB colours inside rect (small rects only)."""
    return {tuple(surface.get_at((x, y)))[:3]
            for x in range(rect.left, rect.right) for y in range(rect.top, rect.bottom)}


def unearth(g, pos, valve=None, closer=0):
    c = g.cell(pos)
    c.surface, c.revealed, c.dug_by = Surface.UNEARTHED, True, 0
    if valve is not None:
        c.valve, c.valve_open = True, valve
        c.valve_closer = None if valve else closer


# --- Board ----------------------------------------------------------------------------------------
def test_board_draws_the_waterworks_on_its_square(app):
    g = app.game
    assert g.waterworks_pos is not None and g.cell(g.waterworks_pos).building
    app.draw(1.0)
    rect = app.renderer.cell_rect(g.waterworks_pos)
    seen = colors_in(app.screen, rect)
    assert theme.WATERWORKS_ROOF in seen and theme.WATERWORKS_WALL in seen


def test_waterworks_square_gets_no_risk_badge(app):
    g = app.game
    g.states[0].upgrades.add(PowerUp.SATELLITE)  # risk grades are drawn in ghost view from now on
    drawn = []
    app.renderer.risk_badge = lambda grade: drawn.append(grade) or pygame.Surface((4, 4))
    app.renderer.draw(app.screen, 1.0, True, False, app.sprites)
    n_graded = sum(c.risk is not None for row in g.cells for c in row)
    assert len(drawn) == n_graded - 1  # every graded square except the waterworks


def test_open_and_closed_valves_are_green_and_red(app):
    g = pipe_row_map(app, bots=(RandomBot,))
    unearth(g, (5, 12), valve=True)
    unearth(g, (7, 12), valve=False)
    unearth(g, (9, 12))  # no valve
    for ghost in (True, False):  # valves on dug-up squares are public: drawn in both views
        app.renderer.draw(app.screen, 1.0, ghost, False, app.sprites, animate=False)
        r = app.renderer.cell_rect
        assert theme.VALVE_OPEN in colors_in(app.screen, r((5, 12)))
        assert theme.VALVE_CLOSED not in colors_in(app.screen, r((5, 12)))
        assert theme.VALVE_CLOSED in colors_in(app.screen, r((7, 12)))
        assert not {theme.VALVE_OPEN, theme.VALVE_CLOSED} & colors_in(app.screen, r((9, 12)))


def test_buried_closed_valve_shows_only_in_ghost_view(app):
    g = pipe_row_map(app, bots=(RandomBot,))
    unearth(g, (5, 12), valve=False)
    unearth(g, (7, 12), valve=True)
    for pos in ((5, 12), (7, 12)):
        g.cell(pos).surface = Surface.UNTOUCHED  # refilled: the valve stays buried
    r = app.renderer.cell_rect
    app.renderer.draw(app.screen, 1.0, True, False, app.sprites, animate=False)
    assert theme.VALVE_CLOSED in colors_in(app.screen, r((5, 12)))
    assert theme.VALVE_OPEN not in colors_in(app.screen, r((7, 12)))  # buried open valves: nothing
    app.renderer.draw(app.screen, 1.0, False, False, app.sprites, animate=False)
    assert theme.VALVE_CLOSED not in colors_in(app.screen, r((5, 12)))


def test_valve_actions_get_light_effects(manual):
    g = pipe_row_map(manual)
    renderer = manual.renderer
    renderer.effects.clear()
    # The human stands on the pipe row: dig the square to the right and work its valve
    hold(manual, pygame.K_e, pygame.K_d)
    hold(manual, pygame.K_v, pygame.K_d)
    hold(manual, pygame.K_c, pygame.K_d)
    hold(manual, pygame.K_o, pygame.K_d)
    kinds = [(e.kind, e.spin) for e in renderer.effects if e.pos == RIGHT]
    assert ("ping", 1) in kinds  # install
    assert ("turn", -1) in kinds and ("turn", 1) in kinds  # close turns clockwise, open counter-clockwise
    assert len(renderer.effects) <= 6  # keep it light: one effect per valve action (+ the dig dust)
    manual.draw(time.perf_counter())  # the "turn" effect draws


def test_reopen_bonus_shows_points(manual):
    g = pipe_row_map(manual, leak=(14, 12))
    unearth(g, RIGHT, valve=False)
    g.cell(RIGHT).valve_bonus = True  # its leak was fixed: reopening pays
    manual.renderer.effects.clear()
    hold(manual, pygame.K_o, pygame.K_d)
    assert g.states[0].valves_reopened == 1
    points = [e for e in manual.renderer.effects if e.kind == "points"]
    assert points and points[0].text == f"+{g.config.points_valve_reopened}"


# --- Toasts ---------------------------------------------------------------------------------------
def make_app(**cfg):
    return app_module.App([RandomBot], config=GameConfig(**cfg), seed=3)


def test_new_leak_toast():
    a = make_app(extra_leak_moves=(3,), punk_spawn_move=0)
    try:
        a._toast = ("", 0.0, theme.GOOD)  # (drop a possible "could not load bots" toast)
        a.update(0.0, 1.0)
        assert a._toast[0] == ""  # a fresh round announces nothing
        for _ in range(3):
            a.step_once()
            a.update(0.0, 1.0)
        assert len(a.game.leak_positions) == a.config.n_leaks + 1
        assert a._toast[0] == a.t("toast_new_leak") and a._toast[2] == theme.WATER
        a._toast = ("", 0.0, theme.GOOD)
        a.update(0.0, 1.0)
        assert a._toast[0] == ""  # announced once
        a.new_game()  # a new round with fewer leaks so far: no toast either
        a.update(0.0, 1.0)
        assert a._toast[0] == ""
    finally:
        pygame.quit()


def test_punk_and_new_leak_on_the_same_move_both_show():
    a = make_app(extra_leak_moves=(2,), punk_spawn_move=2)
    try:
        a._toast = ("", 0.0, theme.GOOD)
        a.set_language("ja")
        for _ in range(2):
            a.step_once()
        a.update(0.0, 1.0)
        assert a._toast[0] == a.t("toast_punk")  # first come, first shown
        a._toast = (a._toast[0], 0.0, a._toast[2])  # let it expire
        a.update(0.0, 1.0)
        assert a._toast[0] == a.t("toast_new_leak") == "どこかで新しい漏水が発生!"
        a.draw(time.perf_counter())
    finally:
        pygame.quit()


# --- Manual valve keys ----------------------------------------------------------------------------
def test_valve_keys_map_to_valve_actions():
    controls = ManualControls()
    dirs = {pygame.K_w: Dir.UP, pygame.K_a: Dir.LEFT, pygame.K_s: Dir.DOWN, pygame.K_d: Dir.RIGHT}
    for mod, make in ((pygame.K_v, Action.install_valve), (pygame.K_c, Action.close_valve),
                      (pygame.K_o, Action.open_valve)):
        assert controls.key_down(mod) is None  # a modifier alone is no move
        for key, d in dirs.items():
            assert controls.key_down(key) == make(d)
        controls.key_up(mod)
    # Valve keys never clash with the other game keys or the hotkeys kept in manual mode
    kept = {pygame.K_h, pygame.K_l, pygame.K_p, pygame.K_n, pygame.K_b, pygame.K_ESCAPE}
    assert not kept & set(MODIFIER_KEYS)
    assert {pygame.K_v, pygame.K_c, pygame.K_o} <= set(MODIFIER_KEYS)
    assert {mod for _, _, mod in HELP_ROWS if mod is not None} == set(MODIFIER_KEYS)  # each has a help row


def test_install_close_open_fix_with_the_keyboard(manual):
    leak = (14, 12)
    g = pipe_row_map(manual, leak=leak)  # human on (12, 12): the pipe row
    st = g.states[0]
    # Walk next to the leak: the human can't stand in holes, so work from the row above
    for key in (pygame.K_w, pygame.K_d):
        down(manual, key)
    assert st.pos == (13, 11)
    hold(manual, pygame.K_e, pygame.K_s)  # dig (13, 12), the leak's left neighbour
    hold(manual, pygame.K_v, pygame.K_s)
    assert manual.game.history[-1].action == Action.install_valve(Dir.DOWN)
    assert g.cell(RIGHT).valve and g.cell(RIGHT).valve_open
    hold(manual, pygame.K_c, pygame.K_s)
    assert manual.game.history[-1].action == Action.close_valve(Dir.DOWN)
    assert not g.cell(RIGHT).valve_open and g.cell(RIGHT).valve_closer == 0
    # The right neighbour (15, 12) too, then dig up and fix the leak from above
    down(manual, pygame.K_d)
    down(manual, pygame.K_d)
    hold(manual, pygame.K_e, pygame.K_s)
    hold(manual, pygame.K_v, pygame.K_s)
    hold(manual, pygame.K_c, pygame.K_s)
    down(manual, pygame.K_a)
    hold(manual, pygame.K_e, pygame.K_s)
    hold(manual, pygame.K_r, pygame.K_s)
    assert g.cell(leak).pipe_state is PipeState.FIXED and st.invalid == 0
    score = st.score
    down(manual, pygame.K_a)
    hold(manual, pygame.K_o, pygame.K_s)  # reopen (13, 12): +5
    assert manual.game.history[-1].action.type is ActionType.OPEN_VALVE
    assert st.score == score + g.config.points_valve_reopened and st.valves_reopened == 1
    hold(manual, pygame.K_o, pygame.K_s)  # already open: invalid, red toast
    assert st.invalid == 1 and manual._toast[2] == theme.DANGER


def test_buying_only_works_on_the_waterworks(manual):
    g = pipe_row_map(manual, waterworks=RIGHT)
    st = g.states[0]
    st.score = 100
    down(manual, pygame.K_1)
    assert not st.upgrades and "waterworks" in manual._toast[0]
    down(manual, pygame.K_d)  # onto the waterworks
    down(manual, pygame.K_1)
    assert st.upgrades == {PowerUp.UT}
    manual.draw(time.perf_counter())  # human drawn on top of the building


# --- Sidebar text fits ----------------------------------------------------------------------------
@pytest.mark.parametrize("lang", ["en", "ja"])
def test_valve_labels_fit_in_both_languages(app, lang):
    app.set_language(lang)
    portrait_w = 8 + 44 + 10 + 16  # card text starts after the portrait; the valves line after its icon
    card_text_w = app_module.SIDEBAR_W - portrait_w - 10
    for key in ("card_valves_live", "card_valves_final"):
        assert theme.font(12).size(app.t(key, r=99, c=99, pen=990))[0] <= card_text_w, key
    assert theme.font(12, bold=True).size(app.t("toast_new_leak"))[0] <= app_module.SIDEBAR_W
    panel_w = app_module.SIDEBAR_W - 20
    assert theme.font(11).size(app.t("sim_seeds", s=999_999, e=1_000_098))[0] <= panel_w
    assert theme.font(12).size(app.t("sim_progress", d=100, t=100, s=999_999, e=1_000_098))[0] <= panel_w
    title_w = theme.font(12, bold=True).size(app.t("sim_title"))[0]
    head = app.t("sim_head", g=1000, sec="99.9") + "  " + app.t("sim_cancelled")
    assert title_w + 12 + theme.font(11).size(head)[0] <= panel_w
    assert theme.font(11, bold=True).size(app.t("help_note_buy"))[0] <= panel_w  # same check as help_note


@pytest.mark.parametrize("lang", ["en", "ja"])
def test_cards_show_valve_line_live_and_at_end(app, lang, monkeypatch):
    app.set_language(lang)
    g = app.game
    texts = []
    real = app._text
    monkeypatch.setattr(app, "_text", lambda text, *a, **k: texts.append(text) or real(text, *a, **k))
    pos = next((x, y) for y, row in enumerate(g.cells) for x, c in enumerate(row)
               if c.has_pipe and not c.building and (x, y) not in g.leak_positions)
    unearth(g, pos, valve=False, closer=1)
    app.draw(1.0)
    assert app.t("card_valves_live", r=0, c=1, pen=g.config.penalty_closed_valve) in texts
    assert app.t("card_valves_live", r=0, c=0, pen=0) in texts  # the other bot: nothing pending
    st = g.states[1]
    st.closed_valves, st.valves_reopened, g.over = 3, 2, True  # as charged by the end of the round
    texts.clear()
    app.draw(2.0)
    assert app.t("card_valves_final", r=st.valves_reopened, c=st.closed_valves,
                 pen=st.closed_valves * g.config.penalty_closed_valve) in texts


def test_sim_panel_shows_the_seed_range(app, monkeypatch):
    texts = []
    real = app._text
    monkeypatch.setattr(app, "_text", lambda text, *a, **k: texts.append(text) or real(text, *a, **k))
    app.toggle_simulation()
    first = app.sim.seed
    app.draw(1.0)
    assert app.t("sim_progress", d=0, t=5, s=first, e=first + 4) in texts
    while app.simulating:
        app.update(1 / 60, 1.0)
    texts.clear()
    app.draw(2.0)
    assert app.t("sim_seeds", s=first, e=first + 4) in texts


@pytest.mark.parametrize("lang", ["en", "ja"])
def test_help_box_with_valve_rows_fits(manual, lang, tmp_path):
    manual.set_language(lang)
    down(manual, pygame.K_c)  # held valve modifier row is highlighted
    manual.draw(time.perf_counter())
    panel_bottom = manual.screen.get_height() - 58 - 8
    assert manual.help_bottom <= panel_bottom
    # The scoreboard card above the help box still has room for the valves line
    help_top = manual.screen.get_height() - 58 - app_module.App.help_box_height()
    assert manual.card_rects[0].h >= 80 and manual.card_rects[0].bottom <= help_top
    pygame.image.save(manual.screen, str(tmp_path / f"manual_valves_{lang}.png"))


# --- Rulebook -------------------------------------------------------------------------------------
@pytest.mark.parametrize("lang", ["en", "ja"])
def test_rulebook_covers_the_new_rules(lang):
    cfg = GameConfig()
    left, right = rulebook(lang, cfg)
    labels = {label for _, items in left + right for label, _ in items}
    text = " ".join(t for _, items in left + right for _, t in items)
    points = {f"+{cfg.points_leak_fixed}", f"+{cfg.points_valve_reopened}", f"-{cfg.penalty_closed_valve}"}
    assert points <= labels
    assert str(cfg.satellite_refresh) in text
    assert all(str(m) in text for m in cfg.extra_leak_moves)
    assert ("waterworks" if lang == "en" else "水道局") in text
    if lang == "en":
        assert {"VALVE", "CLOSE", "OPEN"} <= labels and "200, 300 and 400" in text
