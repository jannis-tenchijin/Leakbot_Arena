"""Colours and fonts for the retro arcade look."""

from __future__ import annotations

import sys
from pathlib import Path

import pygame

# Arcade night background + panels
BG = (14, 16, 38)
PANEL = (26, 28, 62)
PANEL_EDGE = (62, 66, 128)
TEXT = (236, 236, 246)
MUTED = (150, 156, 196)
ACCENT = (255, 212, 0)
DANGER = (240, 72, 84)
GOOD = (80, 210, 110)

# Ground tiles: checkered lawn (untouched) and dirt pits (unearthed)
GRASS = (92, 176, 74)
GRASS_ALT = (84, 164, 68)
GRASS_DARK = (60, 128, 52)
GRASS_LINE = (74, 150, 62)
DIRT = (150, 96, 52)
DIRT_SPECK = (70, 44, 26)
HOLE = (110, 70, 40)
HOLE_DARK = (62, 38, 22)
HOLE_RIM = (150, 100, 58)

# Pipes (steel water main)
PIPE_LIGHT = (222, 234, 242)
PIPE_MID = (136, 166, 188)
PIPE_DARK = (66, 92, 116)
PIPE_LINE = (20, 30, 42)
FITTING = (110, 136, 158)
WATER = (70, 170, 255)
CLAMP = (72, 204, 96)

# One colour per seat
BOT_COLORS = [
    (255, 212, 0),
    (0, 214, 255),
    (255, 118, 196),
    (255, 156, 40),
    (124, 228, 120),
    (188, 132, 255),
    (255, 92, 92),
    (236, 236, 236),
]

_MONO = "menlo,consolas,dejavusansmono,couriernew,monospace"
_FONT_DIR = Path(__file__).resolve().parent / "fonts"
# Bundled Japanese font (M PLUS Rounded 1c, SIL OFL 1.1), subset by tools/subset_font.py
JP_FONT_FILES = {
    "Regular": _FONT_DIR / "MPLUSRounded1c-Regular.subset.ttf",
    "Bold": _FONT_DIR / "MPLUSRounded1c-Bold.subset.ttf",
}
_font_cache: dict[tuple[bool, int, bool], pygame.font.Font] = {}
_language = "en"


def set_language(lang: str) -> None:
    """Switch the font family used by font(): Japanese needs the bundled CJK font."""
    global _language
    _language = lang


def reset_fonts() -> None:
    """Drop cached fonts (they die with pygame.quit(); call after re-initialising pygame)."""
    _font_cache.clear()


def _default_font(size: int, bold: bool) -> pygame.font.Font:
    # pygame's built-in font renders at ~0.69x its nominal size, so scale it up to match
    f = pygame.font.Font(None, round(size * 1.45))
    f.set_bold(bold)
    return f


def font(size: int, bold: bool = False, japanese: bool | None = None) -> pygame.font.Font:
    """Cached UI font for the current language (or force the Japanese font with japanese=True).

    English uses a system monospace font on the desktop and pygame's default font in the
    browser (no system fonts there); Japanese always uses the bundled M PLUS Rounded 1c.
    """
    jp = _language == "ja" if japanese is None else japanese
    key = (jp, size, bold)
    if key not in _font_cache:
        if jp:
            _font_cache[key] = pygame.font.Font(str(JP_FONT_FILES["Bold" if bold else "Regular"]), size)
        elif sys.platform == "emscripten":
            _font_cache[key] = _default_font(size, bold)
        else:
            try:
                _font_cache[key] = pygame.font.SysFont(_MONO, size, bold=bold)
            except Exception:
                _font_cache[key] = _default_font(size, bold)
    return _font_cache[key]
