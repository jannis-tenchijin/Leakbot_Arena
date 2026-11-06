"""Board drawing: ground tiles, shaded pipes, bots, detect marks and action effects."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from pathlib import Path

import pygame

from ..game import Game, TurnEvent
from ..model import ActionType, Dir, PipeState, Pos, Surface
from . import theme

_STRAIGHT = {Dir.UP.bit | Dir.DOWN.bit, Dir.LEFT.bit | Dir.RIGHT.bit}


def _lerp(a: tuple[int, ...], b: tuple[int, ...], t: float) -> tuple[int, ...]:
    return tuple(int(x + (y - x) * t) for x, y in zip(a, b))


@dataclass
class Effect:
    """A short-lived animation anchored to a board cell."""

    kind: str  # "ping", "dust", "spark", "fill", "points", "invalid"
    pos: Pos
    start: float
    duration: float
    color: tuple[int, int, int]
    text: str = ""
    particles: list[tuple[float, float]] = field(default_factory=list)


class BoardRenderer:
    """Draws one Game onto a surface. Rebuild with ``set_game`` when the map changes."""

    MAX_EFFECTS = 80

    def __init__(self, origin: tuple[int, int], tile: int) -> None:
        self.origin = origin
        self.tile = tile
        self.pipe_width = max(6, int(tile * 0.46)) // 2 * 2
        self.ghost_alpha = 80
        self.effects: list[Effect] = []
        self._tween: dict[int, tuple[tuple[float, float], tuple[float, float], float, float]] = {}
        self._sprites: dict[tuple[Path | None, int], pygame.Surface | None] = {}
        self._build_tiles()
        self.game: Game | None = None
        self.pipe_layer: pygame.Surface | None = None

    # --- Geometry ----------------------------------------------------------------------------
    def cell_rect(self, pos: Pos) -> pygame.Rect:
        return pygame.Rect(self.origin[0] + pos[0] * self.tile, self.origin[1] + pos[1] * self.tile, self.tile, self.tile)

    def cell_center(self, pos: Pos) -> tuple[float, float]:
        r = self.cell_rect(pos)
        return r.x + self.tile / 2, r.y + self.tile / 2

    def board_rect(self) -> pygame.Rect:
        assert self.game is not None
        cfg = self.game.config
        return pygame.Rect(self.origin, (cfg.width * self.tile, cfg.height * self.tile))

    # --- Static art --------------------------------------------------------------------------
    def _build_tiles(self) -> None:
        t = self.tile
        rng = random.Random(7)
        # Untouched ground: calm checkered lawn with a few tufts, so dug holes stand out
        self.ground_tiles = []
        for checker in (0, 1):
            for _ in range(2):  # tuft variants so the field does not look tiled
                s = pygame.Surface((t, t))
                s.fill(theme.GRASS if checker else theme.GRASS_ALT)
                for _ in range(2):
                    tx, ty = rng.randrange(3, t - 5), rng.randrange(3, t - 5)
                    pygame.draw.line(s, theme.GRASS_DARK, (tx, ty + 3), (tx + 1, ty), 1)
                    pygame.draw.line(s, theme.GRASS_DARK, (tx + 3, ty + 3), (tx + 2, ty), 1)
                pygame.draw.line(s, theme.GRASS_LINE, (t - 1, 0), (t - 1, t))
                pygame.draw.line(s, theme.GRASS_LINE, (0, t - 1), (t, t - 1))
                self.ground_tiles.append(s)

        # Unearthed: a dirt pit with a shadowed back wall and a lit rim
        hole = pygame.Surface((t, t))
        hole.fill(theme.HOLE)
        for _ in range(3):
            pygame.draw.rect(hole, theme.DIRT_SPECK, (rng.randrange(3, t - 4), rng.randrange(t // 2, t - 4), 2, 2))
        pygame.draw.rect(hole, theme.HOLE_DARK, (0, 0, t, max(3, t // 4)))
        pygame.draw.rect(hole, theme.HOLE_RIM, (0, 0, t, t), width=2)
        self.hole_tile = hole

        # Refilled: walkable again, but a patch of fresh soil shows it was dug before
        refilled = self.ground_tiles[0].copy()
        patch = pygame.Rect(0, 0, t - 6, t - 6)
        patch.center = (t // 2, t // 2)
        pygame.draw.rect(refilled, theme.HOLE_RIM, patch, border_radius=max(2, t // 6))
        for _ in range(4):
            pygame.draw.rect(refilled, theme.HOLE, (rng.randrange(5, t - 7), rng.randrange(5, t - 7), 2, 2))
        self.refilled_tile = refilled

        # Pipe gradient strips: lit in the upper middle, dark at the rims -> looks round
        pw = self.pipe_width
        strip = pygame.Surface((t, pw), pygame.SRCALPHA)
        for i in range(pw):
            u = i / (pw - 1)
            k = max(0.0, 1 - abs(u - 0.38) / 0.62)
            color = _lerp(theme.PIPE_DARK, theme.PIPE_LIGHT, k**1.4)
            pygame.draw.line(strip, color, (0, i), (t, i))
        pygame.draw.line(strip, theme.PIPE_LINE, (0, 0), (t, 0), 2)
        pygame.draw.line(strip, theme.PIPE_LINE, (0, pw - 1), (t, pw - 1), 2)
        self.h_strip = strip
        self.v_strip = pygame.transform.rotate(strip, 90)

    def set_game(self, game: Game) -> None:
        self.game = game
        self.effects.clear()
        self._tween.clear()
        self._build_pipe_layer()

    def _build_pipe_layer(self) -> None:
        """Pre-render every pipe once; frames only blit regions of this layer."""
        assert self.game is not None
        g, t, pw = self.game, self.tile, self.pipe_width
        layer = pygame.Surface(self.board_rect().size, pygame.SRCALPHA)
        half = t // 2
        for y, row in enumerate(g.cells):
            for x, cell in enumerate(row):
                m = cell.pipe_mask
                if not m:
                    continue
                ox, oy = x * t, y * t
                if m in _STRAIGHT:
                    if m & Dir.LEFT.bit:
                        layer.blit(self.h_strip, (ox, oy + half - pw // 2))
                    else:
                        layer.blit(self.v_strip, (ox + half - pw // 2, oy))
                    continue
                # Elbows, tees and crosses: arms to each linked side + a fitting on top
                for d in Dir:
                    if not m & d.bit:
                        continue
                    if d is Dir.LEFT:
                        layer.blit(self.h_strip, (ox, oy + half - pw // 2), (0, 0, half, pw))
                    elif d is Dir.RIGHT:
                        layer.blit(self.h_strip, (ox + half, oy + half - pw // 2), (0, 0, t - half, pw))
                    elif d is Dir.UP:
                        layer.blit(self.v_strip, (ox + half - pw // 2, oy), (0, 0, pw, half))
                    else:
                        layer.blit(self.v_strip, (ox + half - pw // 2, oy + half), (0, 0, pw, t - half))
                fit = pygame.Rect(0, 0, pw + 4, pw + 4)
                fit.center = (ox + half, oy + half)
                pygame.draw.rect(layer, theme.FITTING, fit, border_radius=3)
                pygame.draw.rect(layer, theme.PIPE_LIGHT, fit.inflate(-6, -6), width=1, border_radius=2)
                pygame.draw.rect(layer, theme.PIPE_LINE, fit, width=2, border_radius=3)
        self.pipe_layer = layer

    # --- Sprites -----------------------------------------------------------------------------
    def sprite(self, path: Path | None, size: int) -> pygame.Surface | None:
        key = (path, size)
        if key not in self._sprites:
            img = None
            if path is not None:
                try:
                    img = pygame.transform.smoothscale(pygame.image.load(str(path)).convert_alpha(), (size, size))
                except (pygame.error, FileNotFoundError):
                    img = None
            self._sprites[key] = img
        return self._sprites[key]

    def forget_sprites(self) -> None:
        self._sprites.clear()

    # --- Events -> animations ----------------------------------------------------------------
    def on_turn(self, event: TurnEvent, now: float, move_time: float) -> None:
        """Turn a game event into effects (and a smooth walk for moves)."""
        color = theme.BOT_COLORS[event.bot_id % len(theme.BOT_COLORS)]
        res = event.result
        if not res.success:
            self._add(Effect("invalid", event.from_pos, now, 0.3, theme.DANGER))
            return
        kind = event.action.type
        if kind is ActionType.MOVE:
            if move_time > 0:
                self._tween[event.bot_id] = (self.cell_center(event.from_pos), self.cell_center(event.target), now, move_time)
        elif kind is ActionType.DETECT:
            self._add(Effect("ping", event.target, now, 0.45, color if res.detected_pipe else theme.MUTED))
        elif kind is ActionType.UNEARTH:
            rng = random.Random(event.turn)
            parts = [(rng.uniform(-1, 1), rng.uniform(-1.6, -0.6)) for _ in range(7)]
            self._add(Effect("dust", event.target, now, 0.45, theme.DIRT, particles=parts))
        elif kind is ActionType.FIX:
            self._add(Effect("spark", event.target, now, 0.6, theme.CLAMP))
        elif kind is ActionType.REFILL:
            self._add(Effect("fill", event.target, now, 0.3, theme.DIRT))
        if res.points:
            self._add(Effect("points", event.target, now, 0.9, color, text=f"+{res.points}"))

    def _add(self, effect: Effect) -> None:
        self.effects.append(effect)
        if len(self.effects) > self.MAX_EFFECTS:
            del self.effects[: len(self.effects) - self.MAX_EFFECTS]

    # --- Frame -------------------------------------------------------------------------------
    def draw(
        self,
        surf: pygame.Surface,
        now: float,
        ghost_pipes: bool,
        show_marks: bool,
        sprites: list[Path | None],
        animate: bool = True,
    ) -> None:
        assert self.game is not None and self.pipe_layer is not None
        g, t = self.game, self.tile
        ox, oy = self.origin
        board = self.board_rect()
        pygame.draw.rect(surf, theme.PANEL_EDGE, board.inflate(8, 8), width=4, border_radius=4)

        # 1) Ground
        for y, row in enumerate(g.cells):
            for x, cell in enumerate(row):
                pos = (ox + x * t, oy + y * t)
                if cell.surface is Surface.UNEARTHED:
                    surf.blit(self.hole_tile, pos)
                elif cell.revealed:
                    surf.blit(self.refilled_tile, pos)
                else:
                    checker = (x + y) % 2
                    surf.blit(self.ground_tiles[checker * 2 + (x * 7 + y * 13) % 2], pos)

        # 2) Buried pipes for the audience (game-master toggle)
        if ghost_pipes:
            self.pipe_layer.set_alpha(self.ghost_alpha)
            surf.blit(self.pipe_layer, board.topleft)
            self.pipe_layer.set_alpha(255)  # not None: that would disable per-pixel blending

        # 3) Unearthed pipes at full strength + their condition
        pulse = 0.5 + 0.5 * math.sin(now * 6) if animate else 1.0
        for y, row in enumerate(g.cells):
            for x, cell in enumerate(row):
                if not cell.has_pipe:
                    continue
                rect = self.cell_rect((x, y))
                if cell.surface is Surface.UNEARTHED:
                    surf.blit(self.pipe_layer, rect.topleft, rect.move(-ox, -oy))
                    if cell.pipe_state is PipeState.BROKEN:
                        self._draw_leak(surf, rect, now, 255)
                        red = _lerp(theme.HOLE_RIM, theme.DANGER, pulse)
                        pygame.draw.rect(surf, red, rect, width=2)  # pulsing alarm frame
                    elif cell.pipe_state is PipeState.FIXED:
                        self._draw_clamp(surf, rect, cell.pipe_mask)
                elif ghost_pipes and cell.pipe_state is PipeState.BROKEN:
                    self._draw_leak(surf, rect, now, int(70 + 60 * pulse))

        # 4) Private detect marks (audience only; bots never see each other's)
        if show_marks:
            self._draw_marks(surf)

        # 5) Bots, then effects on top
        for bot_id, state in enumerate(g.states):
            self._draw_bot(surf, bot_id, state.pos, state.facing, now, sprites[bot_id], animate)
        self._draw_effects(surf, now)

    def _draw_leak(self, surf: pygame.Surface, rect: pygame.Rect, now: float, alpha: int) -> None:
        t = self.tile
        layer = pygame.Surface((t, t), pygame.SRCALPHA)
        c = t // 2
        # Puddle + crack + spurting droplets
        pygame.draw.ellipse(layer, (*theme.WATER, min(alpha, 110)), (2, t * 0.55, t - 4, t * 0.4))
        pts = [(c - 5, c - 4), (c - 1, c), (c - 3, c + 2), (c + 3, c + 5)]
        pygame.draw.lines(layer, (*theme.DANGER, alpha), False, pts, 2)
        for i in range(3):
            phase = (now * 1.6 + i / 3) % 1.0
            dx = (i - 1) * t * 0.22 * phase
            dy = -t * 0.35 * math.sin(phase * math.pi)
            pygame.draw.circle(layer, (*theme.WATER, alpha), (c + dx, c - 2 + dy), max(2, t // 10))
        surf.blit(layer, rect.topleft)

    def _draw_clamp(self, surf: pygame.Surface, rect: pygame.Rect, mask: int) -> None:
        pw, cx, cy = self.pipe_width, rect.centerx, rect.centery
        horizontal = bool(mask & (Dir.LEFT.bit | Dir.RIGHT.bit))
        vertical = bool(mask & (Dir.UP.bit | Dir.DOWN.bit))
        if horizontal and not vertical:
            band = pygame.Rect(0, 0, max(4, pw // 3), pw + 6)
        elif vertical and not horizontal:
            band = pygame.Rect(0, 0, pw + 6, max(4, pw // 3))
        else:
            band = pygame.Rect(0, 0, pw + 8, pw + 8)
            band.center = (cx, cy)
            pygame.draw.rect(surf, theme.CLAMP, band, width=3, border_radius=4)
            return
        band.center = (cx, cy)
        pygame.draw.rect(surf, theme.CLAMP, band, border_radius=2)
        pygame.draw.rect(surf, theme.PIPE_LINE, band, width=1, border_radius=2)

    def _draw_marks(self, surf: pygame.Surface) -> None:
        g, t = self.game, self.tile
        r = max(2, t // 9)
        corners = [(0.2, 0.8), (0.8, 0.8), (0.2, 0.2), (0.8, 0.2)]
        for bot_id, state in enumerate(g.states):
            color = theme.BOT_COLORS[bot_id % len(theme.BOT_COLORS)]
            fx, fy = corners[bot_id % 4]
            for (x, y), had_pipe in state.detected.items():
                if g.cells[y][x].surface is not Surface.UNTOUCHED:
                    continue
                center = (self.origin[0] + (x + fx) * t, self.origin[1] + (y + fy) * t)
                pygame.draw.circle(surf, (10, 10, 20), center, r + 1)
                pygame.draw.circle(surf, color, center, r, width=0 if had_pipe else 1)

    def _bot_center(self, bot_id: int, pos: Pos, now: float) -> tuple[float, float]:
        tween = self._tween.get(bot_id)
        if tween:
            (x0, y0), (x1, y1), start, dur = tween
            k = (now - start) / dur
            if k < 1:
                k = k * k * (3 - 2 * k)  # smoothstep
                return x0 + (x1 - x0) * k, y0 + (y1 - y0) * k
            del self._tween[bot_id]
        return self.cell_center(pos)

    def _draw_bot(
        self, surf: pygame.Surface, bot_id: int, pos: Pos, facing: Dir, now: float, sprite: Path | None, animate: bool
    ) -> None:
        t = self.tile
        cx, cy = self._bot_center(bot_id, pos, now)
        bob = math.sin(now * 5 + bot_id) * t * 0.04 if animate else 0
        color = theme.BOT_COLORS[bot_id % len(theme.BOT_COLORS)]
        # Shadow keeps bots readable on both dirt and holes
        pygame.draw.ellipse(surf, (10, 8, 6), (cx - t * 0.36, cy + t * 0.28, t * 0.72, t * 0.16))
        img = self.sprite(sprite, t - 2)
        if img is not None:
            surf.blit(img, img.get_rect(center=(cx, cy + bob)))
            return
        draw_robot(surf, (cx, cy + bob), t, color, facing)

    def _draw_effects(self, surf: pygame.Surface, now: float) -> None:
        alive = []
        for e in self.effects:
            k = (now - e.start) / e.duration
            if k >= 1:
                continue  # expired
            alive.append(e)
            _draw_effect(surf, e, max(0.0, k), self.cell_center(e.pos), self.tile)
        self.effects = alive


def draw_robot(surf: pygame.Surface, center: tuple[float, float], t: int, color: tuple[int, int, int], facing: Dir) -> None:
    """Default bot art: a chunky little digger robot that looks where it acts."""
    cx, cy = center
    body = pygame.Rect(0, 0, int(t * 0.72), int(t * 0.62))
    body.center = (int(cx), int(cy + t * 0.04))
    dark = _lerp(color, (0, 0, 0), 0.45)
    # Antenna
    pygame.draw.line(surf, dark, (body.centerx, body.top), (body.centerx, body.top - t * 0.16), 2)
    pygame.draw.circle(surf, theme.DANGER, (body.centerx, body.top - t * 0.18), max(2, t // 12))
    # Body + treads
    pygame.draw.rect(surf, color, body, border_radius=max(3, t // 6))
    pygame.draw.rect(surf, dark, body, width=2, border_radius=max(3, t // 6))
    tread = pygame.Rect(body.x - 1, body.bottom - max(3, t // 8), body.w + 2, max(3, t // 8))
    pygame.draw.rect(surf, (34, 34, 40), tread, border_radius=2)
    # Visor with eyes looking toward the last action
    visor = pygame.Rect(0, 0, int(body.w * 0.78), int(body.h * 0.4))
    visor.center = (body.centerx, body.y + int(body.h * 0.38))
    pygame.draw.rect(surf, (20, 22, 40), visor, border_radius=3)
    dx, dy = facing.delta
    er = max(2, t // 10)
    for side in (-1, 1):
        ex = visor.centerx + side * visor.w * 0.22 + dx * er * 0.6
        ey = visor.centery + dy * er * 0.5
        pygame.draw.circle(surf, (240, 248, 255), (ex, ey), er)
        pygame.draw.circle(surf, (10, 10, 20), (ex + dx * er * 0.45, ey + dy * er * 0.45), max(1, er // 2))


def _draw_effect(surf: pygame.Surface, e: Effect, k: float, center: tuple[float, float], t: int) -> None:
    cx, cy = center
    if e.kind == "ping":
        pygame.draw.circle(surf, e.color, (cx, cy), int(t * (0.15 + 0.5 * k)), width=2)
    elif e.kind == "dust":
        for vx, vy in e.particles:
            px = cx + vx * t * 0.6 * k
            py = cy + (vy * k + 1.8 * k * k) * t * 0.6
            pygame.draw.rect(surf, e.color, (px, py, max(2, t // 9), max(2, t // 9)))
    elif e.kind == "spark":
        r = t * (0.2 + 0.4 * k)
        for i in range(8):
            a = i * math.pi / 4
            pygame.draw.line(
                surf, theme.ACCENT if i % 2 else e.color, (cx + math.cos(a) * r * 0.5, cy + math.sin(a) * r * 0.5),
                (cx + math.cos(a) * r, cy + math.sin(a) * r), 2,
            )
    elif e.kind == "fill":
        size = int(t * (1 - k))
        if size > 0:
            pygame.draw.rect(surf, e.color, pygame.Rect(0, 0, size, size).move(cx - size / 2, cy - size / 2))
    elif e.kind == "points":
        img = theme.font(max(11, t // 2), bold=True).render(e.text, True, e.color)
        shadow = theme.font(max(11, t // 2), bold=True).render(e.text, True, (0, 0, 0))
        y = cy - t * 0.4 - k * t * 0.9
        surf.blit(shadow, shadow.get_rect(center=(cx + 1, y + 1)))
        surf.blit(img, img.get_rect(center=(cx, y)))
    elif e.kind == "invalid":
        s = t * 0.22
        pygame.draw.line(surf, e.color, (cx - s, cy - t * 0.55 - s), (cx + s, cy - t * 0.55 + s), 3)
        pygame.draw.line(surf, e.color, (cx - s, cy - t * 0.55 + s), (cx + s, cy - t * 0.55 - s), 3)


