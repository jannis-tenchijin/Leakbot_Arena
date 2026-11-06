"""Bot discovery and the SETUP overlay (mode + bot per seat)."""

import os
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pytest

pygame = pytest.importorskip("pygame")

from bots.government_bot import GovernmentBot  # noqa: E402
from bots.random_bot import RandomBot  # noqa: E402
from bots.tenchijin_bot import TenchijinBot  # noqa: E402
from leakbots.gui import app as app_module  # noqa: E402
from leakbots.gui.manual import HumanBot  # noqa: E402
from leakbots.gui.setup import ROYALE_MAX, ROYALE_MIN, GameSetup, mode_for  # noqa: E402
from leakbots.loader import bot_spec, discover_bots  # noqa: E402


# --- Discovery -----------------------------------------------------------------------------------
def test_discover_finds_every_bot_in_bots_folder():
    bots, failed = discover_bots()
    names = {c.__name__ for c in bots}
    assert {"RandomBot", "GovernmentBot", "TenchijinBot", "MyBot"} <= names
    assert failed == []
    assert bot_spec(GovernmentBot) == "government_bot:GovernmentBot"


def test_discover_skips_broken_files(tmp_path, monkeypatch):
    pkg = tmp_path / "tmpbots"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "good_bot.py").write_text(
        "from leakbots import Action, BaseBot\n"
        "class GoodBot(BaseBot):\n"
        "    def act(self, view):\n"
        "        return Action.detect()\n"
    )
    (pkg / "broken_bot.py").write_text("this is not python(\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    bots, failed = discover_bots(pkg, "tmpbots")
    assert [c.__name__ for c in bots] == ["GoodBot"]
    assert failed == ["broken_bot.py"]
    sys.modules.pop("tmpbots", None)


# --- Setup model ---------------------------------------------------------------------------------
AVAILABLE = [GovernmentBot, RandomBot, TenchijinBot]


@pytest.mark.parametrize("n, manual, mode", [(1, False, "single"), (2, False, "duel"), (3, False, "royale"),
                                             (5, False, "royale"), (1, True, "manual")])
def test_mode_follows_line_up(n, manual, mode):
    assert mode_for(n, manual) == mode


def test_setup_starts_from_the_board_and_remembers_each_mode():
    s = GameSetup(AVAILABLE, [RandomBot, RandomBot], manual=False)
    assert s.mode == "duel" and s.seats == [RandomBot, RandomBot]
    s.cycle(1, +1)  # RandomBot -> TenchijinBot
    assert s.seats == [RandomBot, TenchijinBot]
    s.set_mode("single")
    assert len(s.seats) == 1
    s.set_mode("duel")
    assert s.seats == [RandomBot, TenchijinBot]  # picks per mode are kept


def test_cycle_wraps_around():
    s = GameSetup(AVAILABLE, [GovernmentBot], manual=False)
    s.cycle(0, -1)
    assert s.seats == [TenchijinBot]
    s.cycle(0, +1)
    assert s.seats == [GovernmentBot]


def test_royale_seat_limits():
    s = GameSetup(AVAILABLE, [RandomBot], manual=False)
    s.set_mode("royale")
    assert len(s.seats) == ROYALE_MIN and not s.can_remove()
    for _ in range(10):
        s.add_seat()
    assert len(s.seats) == ROYALE_MAX and not s.can_add()
    s.set_mode("duel")
    s.add_seat()
    assert len(s.seats) == 2  # only battle royale changes its seat count


def test_lineup_and_manual():
    s = GameSetup(AVAILABLE, [RandomBot], manual=True)
    assert s.mode == "manual" and s.lineup() == ([], True)
    s.set_mode("single")
    bots, manual = s.lineup()
    assert len(bots) == 1 and not manual


def test_unknown_start_bot_stays_selectable():
    class LocalBot(RandomBot):
        pass

    s = GameSetup(AVAILABLE, [LocalBot], manual=False)
    assert LocalBot in s.available and s.seats == [LocalBot]


# --- Overlay in the app --------------------------------------------------------------------------
@pytest.fixture
def app():
    a = app_module.App([RandomBot], seed=3, sim_rounds=5)
    yield a
    pygame.quit()


def key(app, k):
    app.handle_event(pygame.event.Event(pygame.KEYDOWN, key=k))


def click(app, rect):
    app.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=rect.center))
    app.handle_event(pygame.event.Event(pygame.MOUSEBUTTONUP, button=1, pos=rect.center))


def test_all_bots_are_loaded_even_if_one_was_started(app):
    assert {GovernmentBot, TenchijinBot, RandomBot} <= set(app.setup.available)


def test_setup_key_opens_and_blocks_game_keys(app):
    key(app, pygame.K_b)
    assert app.show_setup
    key(app, pygame.K_SPACE)
    assert not app.playing  # game keys do nothing under the overlay
    key(app, pygame.K_ESCAPE)
    assert not app.show_setup and app.running


@pytest.mark.parametrize("lang", ["en", "ja"])
def test_pick_duel_by_clicking_and_start(app, lang):
    app.set_language(lang)
    setup_button = next(b for b in app.buttons if b.label == app.t("setup"))
    click(app, setup_button.rect)
    assert app.show_setup
    app.draw(1.0)
    duel_tab = next(r for r, _ in app._setup_hits if r.y < 140 and r.x > 300 and r.x < 560)
    click(app, duel_tab)
    app.draw(1.0)
    assert app.setup.mode == "duel" and len(app.setup.seats) == 2
    app.setup.picks["duel"] = [GovernmentBot, TenchijinBot]
    key(app, pygame.K_RETURN)
    assert not app.show_setup and not app.manual
    assert [type(b) for b in app.game.bots] == [GovernmentBot, TenchijinBot]
    app.draw(1.0)


def test_start_manual_from_setup_and_back(app):
    key(app, pygame.K_b)
    app.setup.set_mode("manual")
    key(app, pygame.K_RETURN)
    assert app.manual and [type(b) for b in app.game.bots] == [HumanBot]
    app.toggle_manual()  # MANUAL hands back to the last bot line-up
    assert not app.manual and [type(b) for b in app.game.bots] == [RandomBot]


def test_royale_plus_minus_buttons(app):
    key(app, pygame.K_b)
    app.setup.set_mode("royale")
    app.draw(1.0)
    plus = next(r for r, a in app._setup_hits if a == app.setup.add_seat)
    click(app, plus)
    assert len(app.setup.seats) == ROYALE_MIN + 1
    app.draw(1.0)
    key(app, pygame.K_RETURN)
    assert len(app.game.bots) == ROYALE_MIN + 1


def test_click_outside_closes(app):
    key(app, pygame.K_b)
    app.handle_event(pygame.event.Event(pygame.MOUSEBUTTONUP, button=1, pos=(2, 2)))
    assert not app.show_setup
