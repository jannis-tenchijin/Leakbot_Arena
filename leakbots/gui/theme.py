"""Colours and fonts for the retro arcade look."""

from __future__ import annotations

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
_font_cache: dict[tuple[int, bool], pygame.font.Font] = {}


def reset_fonts() -> None:
    """Drop cached fonts (they die with pygame.quit(); call after re-initialising pygame)."""
    _font_cache.clear()


def font(size: int, bold: bool = False) -> pygame.font.Font:
    """Cached monospace font (falls back to pygame's default font)."""
    key = (size, bold)
    if key not in _font_cache:
        try:
            _font_cache[key] = pygame.font.SysFont(_MONO, size, bold=bold)
        except Exception:
            _font_cache[key] = pygame.font.Font(None, size + 4)
    return _font_cache[key]
