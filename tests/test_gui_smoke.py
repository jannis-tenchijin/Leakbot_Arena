"""Headless GUI smoke test (SDL dummy driver): frames render, controls work, sim thread finishes."""

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pytest

pygame = pytest.importorskip("pygame")

from bots.random_bot import RandomBot  # noqa: E402
from leakbots.gui import app as app_module  # noqa: E402
from leakbots import loader  # noqa: E402


@pytest.fixture
def app():
    a = app_module.App([RandomBot, RandomBot], seed=3, sim_rounds=5)
    yield a
    pygame.quit()


def frames(a, n, t0=10.0):
    for i in range(n):
        now = t0 + i / 60
        a.update(1 / 60, now)
        a.draw(now)
    return now


def test_play_advances_and_renders(app, tmp_path):
    app.toggle_play()
    app.speed = 0.6  # ~40 moves/s
    frames(app, 60)
    assert app.game.turn > 10
    app.ghost_pipes = False
    app.draw(11.0)
    pygame.image.save(app.screen, str(tmp_path / "frame.png"))
    assert (tmp_path / "frame.png").stat().st_size > 0


def test_max_speed_finishes_game_and_shows_game_over(app):
    app.speed = 1.0
    app.toggle_play()
    for i in range(400):
        frames(app, 1, t0=20 + i / 60)
        if app.game.over:
            break
    assert app.game.over and not app.playing
    app.draw(30.0)  # game-over banner path


def test_keyboard_controls(app):
    def key(k):
        app.handle_event(pygame.event.Event(pygame.KEYDOWN, key=k))

    key(pygame.K_s)
    assert app.game.turn == 1
    ghost = app.ghost_pipes
    key(pygame.K_p)
    assert app.ghost_pipes is not ghost
    seed = app.seed
    key(pygame.K_r)
    assert app.seed == seed and app.game.turn == 0
    key(pygame.K_SPACE)
    assert app.playing


def test_language_switch_and_rulebook(app):
    def key(k):
        app.handle_event(pygame.event.Event(pygame.KEYDOWN, key=k))

    for lang in ("en", "ja"):
        app.set_language(lang)
        app.draw(1.0)
        key(pygame.K_h)  # open rulebook
        assert app.show_rules
        app.draw(1.0)
        key(pygame.K_SPACE)  # game keys are ignored while the rulebook is open
        assert not app.playing
        key(pygame.K_ESCAPE)  # Esc closes the rulebook, not the app
        assert not app.show_rules and app.running

    key(pygame.K_l)
    assert app.lang == "en"
    # Clicking the 日本語 half of the switch selects Japanese
    seg = app.lang_switch.rect
    pos = (seg.right - 5, seg.centery)
    app.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=pos))
    assert app.lang == "ja"
    # Rules button opens the rulebook; a click outside the panel closes it
    rb = app.rules_button.rect.center
    app.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=rb))
    app.handle_event(pygame.event.Event(pygame.MOUSEBUTTONUP, button=1, pos=rb))
    assert app.show_rules
    app.handle_event(pygame.event.Event(pygame.MOUSEBUTTONUP, button=1, pos=(2, 2)))
    assert not app.show_rules


@pytest.mark.parametrize("lang", ["en", "ja"])
def test_rulebook_fits_at_readable_size(app, lang):
    app.set_language(lang)
    app.show_rules = True
    app.draw(1.0)
    assert app.rules_text_size >= 12  # the auto-fit should not need to shrink below 12 px


@pytest.mark.parametrize("lang", ["en", "ja"])
def test_sidebar_text_fits(app, lang):
    from leakbots.gui import theme

    app.set_language(lang)
    for key in ("hint_keys1", "hint_keys2", "hint_drop", "hint_web_sprite", "subtitle"):
        size = 12 if key == "subtitle" else 11
        assert theme.font(size).size(app.t(key))[0] <= app_module.SIDEBAR_W, (lang, key)
    logo_w = theme.font(app.logo_size, bold=True, japanese=False).size("LEAKBOTS")[0]
    assert app.sidebar_x + logo_w < app.rules_button.rect.x  # logo clear of the corner buttons
    for button in app.buttons + [app.rules_button]:
        assert theme.font(button.size, bold=True).size(button.label)[0] <= button.rect.w - 8, button.label


def test_simulation_runs_across_frames(app):
    app.toggle_simulation()
    assert app.simulating
    app.draw(4.0)  # progress bar path
    for _ in range(200):
        frames(app, 1)
        if not app.simulating:
            break
    assert app.sim_result is not None and app.sim_result.rounds == 5
    app.draw(5.0)  # results panel path


def test_simulation_cancel(app):
    app.sim_rounds = 10_000
    app.toggle_simulation()
    frames(app, 2)
    app.toggle_simulation()  # second press cancels
    assert not app.simulating and app.sim_result.cancelled


def test_png_drop_sets_sprite(app, tmp_path, monkeypatch):
    sprites = tmp_path / "sprites"
    monkeypatch.setattr(app_module, "SPRITES_DIR", sprites)
    monkeypatch.setattr(loader, "SPRITES_DIR", sprites)
    png = tmp_path / "hero.png"
    img = pygame.Surface((16, 16), pygame.SRCALPHA)
    img.fill((255, 0, 0, 255))
    pygame.image.save(img, str(png))

    app.handle_drop(str(png), bot_id=1)
    assert (sprites / "RandomBot.png").is_file()
    assert app.sprites[0] == sprites / "RandomBot.png"  # same class -> both seats use it
    app.draw(1.0)

    app.handle_drop(str(tmp_path / "notes.txt"), bot_id=0)
    assert "png" in app._toast[0].lower()
