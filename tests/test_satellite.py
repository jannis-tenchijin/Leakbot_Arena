"""Tenchijin satellite power-up: A-E risk grades on pipe squares and the private network reveal.

Engine tests use the hand-made 5x5 map of test_rules.py: a straight pipe along row y=2 with a
leak at (2, 2); bot 0 spawns at (2, 1), directly above the leak.
"""

import os
import random
from collections import Counter

import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")  # GUI smoke tests render headless

import leakbots.game as game_module
from leakbots import Action, BaseBot, Dir, Game, GameConfig, PipeState, PowerUp, SatelliteReading
from leakbots.bot_api import BotView
from leakbots.model import RISK_GRADES
from leakbots.pipes import generate_pipe_masks, pick_leaks

H = Dir.LEFT.bit | Dir.RIGHT.bit
MASKS = [[0] * 5, [0] * 5, [H] * 5, [0] * 5, [0] * 5]
LEAK = (2, 2)
PIPES = {(x, 2) for x in range(5)}
ALL_SQUARES = {(x, y) for y in range(5) for x in range(5)}
CONFIG = GameConfig(width=5, height=5, max_moves=10, n_leaks=1)
SMALL = GameConfig(width=15, height=15, max_moves=1)  # quick maps for the statistics


class IdleBot(BaseBot):
    def act(self, view):
        return None


def make_game(n_bots=1, spawns=((2, 1), (0, 0)), config=CONFIG):
    return Game([IdleBot] * n_bots, config, seed=0, masks=MASKS, leaks=[LEAK], spawns=list(spawns[:n_bots]))


def buy_satellite(g, bot_id=0):
    g.states[bot_id].score = 30  # test shortcut: pretend the bot already earned these
    result = g.apply_action(bot_id, Action.buy(PowerUp.SATELLITE))
    assert result.success, result.message
    return result


def detect(g, bot_id, direction):
    """A detect that covers the square in `direction`, for the 4-way scan and the old 1-way detect."""
    result = g.apply_action(bot_id, Action.detect())  # 4-way scan: needs no direction
    if not result.success:
        result = g.apply_action(bot_id, Action.detect(direction))  # 1-way detect needs one
    assert result.success, result.message
    return result


def grades(g):
    """(intact grades, leak grades, non-pipe grades) of one round."""
    intact, leaks, empty = [], [], []
    for row in g.cells:
        for c in row:
            bucket = empty if not c.has_pipe else leaks if c.pipe_state is PipeState.BROKEN else intact
            bucket.append(c.risk)
    return intact, leaks, empty


# --- Grades -----------------------------------------------------------------------------------
@pytest.mark.parametrize("seed", range(10))
def test_every_pipe_square_has_a_grade_in_its_range(seed):
    intact, leaks, empty = grades(Game([IdleBot], seed=seed))
    assert intact and leaks and set(empty) == {None}
    assert set(intact) <= {"A", "B", "C", "D"}  # intact pipes are never graded E
    assert set(leaks) <= {"B", "C", "D", "E"}  # leaks are never graded A


def test_grades_on_explicit_masks_and_leaks():
    g = make_game()
    assert {pos for pos in ALL_SQUARES if g.cell(pos).risk is not None} == PIPES
    assert g.cell(LEAK).risk in "BCDE"


def test_grade_distribution_matches_risk_decay():
    intact, leaks = Counter(), Counter()
    for seed in range(200):
        i, k, _ = grades(Game([IdleBot], SMALL, seed=seed))
        intact.update(i)
        leaks.update(k)
    n_intact, n_leaks = sum(intact.values()), sum(leaks.values())
    # risk_decay 0.3 -> 70.6 / 21.2 / 6.4 / 1.9 %
    assert 0.60 <= intact["A"] / n_intact <= 0.80
    assert 0.15 <= intact["B"] / n_intact <= 0.28
    assert 0.005 <= intact["D"] / n_intact <= 0.04
    assert 0.55 <= leaks["E"] / n_leaks <= 0.85
    assert leaks["E"] > leaks["D"] > leaks["C"] and intact["A"] > intact["B"] > intact["C"] > intact["D"]


def test_risk_decay_shapes_the_grades():
    certain = GameConfig(risk_decay=1e-9)  # practically always level 0: intact A, leaks E
    intact, leaks, _ = grades(Game([IdleBot], certain, seed=5))
    assert set(intact) == {"A"} and set(leaks) == {"E"}
    with pytest.raises(ValueError):
        GameConfig(risk_decay=0)
    with pytest.raises(ValueError):
        GameConfig(risk_decay=1.5)
    GameConfig(risk_decay=1.0)  # uniform grades are allowed


def test_grades_are_deterministic_per_seed():
    def risk_map(seed):
        return [[c.risk for c in row] for row in Game([IdleBot], seed=seed).cells]

    assert risk_map(11) == risk_map(11)
    assert risk_map(11) != risk_map(12)


def test_grades_do_not_change_map_spawns_or_bot_rngs(monkeypatch):
    seed, cfg = 1234, GameConfig()
    g = Game([IdleBot, IdleBot], cfg, seed=seed)
    # The map comes from the round RNG exactly as before the grades existed
    rng = random.Random(seed)
    masks = generate_pipe_masks(cfg.width, cfg.height, rng, cfg.pipe_coverage)
    assert [[c.pipe_mask for c in row] for row in g.cells] == masks
    assert list(g.leak_positions) == pick_leaks(masks, cfg.n_leaks, rng)
    # Spawns, bot RNGs and the round RNG match a game built without grades (main's behaviour)
    monkeypatch.setattr(game_module, "pick_risk_grades", lambda *args: {})
    plain = Game([IdleBot, IdleBot], cfg, seed=seed)
    assert all(c.risk is None for row in plain.cells for c in row)
    assert [s.pos for s in g.states] == [s.pos for s in plain.states]
    assert [b.rng.getstate() for b in g.bots] == [b.rng.getstate() for b in plain.bots]
    assert g.rng.getstate() == plain.rng.getstate()


# --- Purchase -----------------------------------------------------------------------------------
def test_satellite_costs_25_points():
    g = make_game()
    g.states[0].score = 24
    r = g.apply_action(0, Action.buy(PowerUp.SATELLITE))
    assert not r.success and "costs 25" in r.message
    assert dict(BotView(g, 0).satellite) == {} and g.states[0].score == 24
    g.states[0].score = 25
    r = g.apply_action(0, Action.buy(PowerUp.SATELLITE))
    assert r.success and r.points == -25 and g.states[0].score == 0 and g.states[0].spent == 25


def test_satellite_reveals_the_network_privately():
    g = make_game(n_bots=2)
    view, other = BotView(g, 0), BotView(g, 1)
    assert dict(view.satellite) == {} and view.risk(LEAK) is None  # nothing before buying
    buy_satellite(g)
    sat = view.satellite
    assert set(sat) == PIPES
    for pos, reading in sat.items():
        assert isinstance(reading, SatelliteReading)
        assert reading.pipe_dirs == {Dir.LEFT, Dir.RIGHT}  # aerial view: directions always shown
        assert reading.risk == g.cell(pos).risk == view.risk(pos)
    assert view.risk(LEAK) in "BCDE" and view.risk((0, 0)) is None  # no grade off the pipes
    assert dict(view.detected) == {pos: pos in PIPES for pos in ALL_SQUARES}
    assert g.states[0].discovered >= ALL_SQUARES
    assert g.states[0].score == 5 and g.states[0].pipes_detected == 0  # the reveal pays nothing
    # Other bots only learn that it was bought
    assert other.upgrades_of(0) == {PowerUp.SATELLITE}
    assert dict(other.satellite) == {} and all(other.risk(pos) is None for pos in PIPES)
    assert not set(other.detected) & PIPES
    assert not hasattr(view.cell(LEAK), "risk")  # CellInfo never carries the grade


def test_satellite_squares_pay_no_detect_points():
    g = make_game(n_bots=2, spawns=((2, 1), (1, 3)))
    buy_satellite(g)
    r = detect(g, 0, Dir.DOWN)  # covers the leak square (2, 2)
    assert r.points == 0 and g.states[0].score == 5 and g.states[0].pipes_detected == 0
    # Control: the same kind of detect still pays +1 to a bot without the satellite
    assert detect(g, 1, Dir.UP).points == 1  # covers (1, 2)


def test_grades_stay_fixed_when_a_leak_is_fixed():
    g = make_game()
    buy_satellite(g)
    before = BotView(g, 0).risk(LEAK)
    assert g.apply_action(0, Action.unearth(Dir.DOWN)).success
    assert g.apply_action(0, Action.fix(Dir.DOWN)).success
    assert g.cell(LEAK).pipe_state is PipeState.FIXED
    assert BotView(g, 0).risk(LEAK) == g.cell(LEAK).risk == before


# --- GUI ----------------------------------------------------------------------------------------
@pytest.mark.parametrize("lang", ["en", "ja"])
def test_gui_shows_grades_only_in_ghost_view(lang):
    pygame = pytest.importorskip("pygame")
    from bots.random_bot import RandomBot
    from leakbots.gui import app as app_module
    from leakbots.gui import theme

    app = app_module.App([RandomBot, RandomBot], seed=3, sim_rounds=5, lang=lang)
    try:
        r = app.renderer
        assert not r.show_risk(True)  # nobody owns the satellite yet
        app.draw(1.0)
        buy_satellite(app.game, bot_id=1)
        assert r.show_risk(True) and not r.show_risk(False)
        for grade in RISK_GRADES:  # badges fit inside a tile
            assert max(r.risk_badge(grade).get_size()) < r.tile

        app.ghost_pipes = True
        app.draw(1.0)
        legend = app._draw_risk_legend()
        board = r.board_rect()
        assert legend.top > board.bottom + 4 and legend.bottom <= app.screen.get_height()  # below the frame
        assert legend.right < app.sidebar_x  # clear of the sidebar
        inside = (legend.x + 4, legend.centery)
        assert app.screen.get_at(inside)[:3] == theme.PANEL

        app.ghost_pipes = False  # PIPES: HIDDEN hides grades and legend
        app.draw(1.0)
        assert app.screen.get_at(inside)[:3] == theme.BG
        app.show_marks = True
        app.draw(2.0)
    finally:
        pygame.quit()
