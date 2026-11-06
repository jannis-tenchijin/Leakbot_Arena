"""Translations: same placeholders in both languages, every Japanese glyph in the bundled font."""

import os
import string

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pytest

pygame = pytest.importorskip("pygame")

from leakbots.config import DEFAULT_CONFIG, GameConfig  # noqa: E402
from leakbots.gui import theme  # noqa: E402
from leakbots.gui.i18n import LANGS, STRINGS, all_japanese_text, rulebook  # noqa: E402


def fields(text):
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


def test_every_key_has_both_languages_with_same_placeholders():
    assert set(STRINGS["en"]) == set(STRINGS["ja"])
    for key in STRINGS["en"]:
        assert fields(STRINGS["en"][key]) == fields(STRINGS["ja"][key]), key


@pytest.mark.parametrize("lang", LANGS)
def test_rulebook_uses_live_rule_values(lang):
    cfg = GameConfig(max_rounds=321, points_leak_fixed=42)
    left, right = rulebook(lang, cfg)
    text = " ".join(t for _, items in left + right for label, t in items) + " ".join(
        label for _, items in left + right for label, _ in items
    )
    assert "321" in text and "+42" in text
    assert "{" not in text  # every placeholder filled


def test_bundled_font_has_every_japanese_glyph():
    pygame.font.init()
    for bold in (False, True):
        font = pygame.font.Font(str(theme.JP_FONT_FILES["Bold" if bold else "Regular"]), 14)
        chars = sorted(set(all_japanese_text() + "日本語"))
        missing = [c for c, m in zip(chars, font.metrics("".join(chars))) if m is None]
        assert not missing, f"re-run tools/subset_font.py; missing: {''.join(missing)}"


def test_default_config_rulebook_mentions_permanent_discovery():
    left, right = rulebook("en", DEFAULT_CONFIG)
    scoring = dict(right)["SCORING"]
    assert any("never for a square that was ever unearthed" in text for _, text in scoring)
