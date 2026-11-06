"""Pygame front end: live board, controls, scoreboard and the threaded 100-game simulation."""

from __future__ import annotations

import random
import shutil
import threading
import time
from collections.abc import Sequence
from pathlib import Path

import pygame

from ..bot_api import BaseBot
from ..config import DEFAULT_CONFIG, GameConfig
from ..game import Game
from ..loader import SPRITES_DIR, sprite_path
from ..simulation import SimulationResult, run_games, seat_labels
from . import theme
from .render import BoardRenderer, draw_robot
from .widgets import Button, Slider, draw_progress

MARGIN = 24
SIDEBAR_W = 340
MAX_RATE = 500  # moves per second at the right end of the slider (before "MAX")
FRAME_STEP_BUDGET = 0.012  # seconds per frame the engine may use in MAX mode


class App:
    """The arena window. ``run()`` blocks until the window is closed."""

    def __init__(
        self,
        bot_classes: Sequence[type[BaseBot]],
        config: GameConfig = DEFAULT_CONFIG,
        seed: int | None = None,
        sim_games: int = 100,
    ) -> None:
        pygame.init()
        theme.reset_fonts()
        self.bot_classes = list(bot_classes)
        self.config = config
        self.sim_games = sim_games
        self.labels = seat_labels(self.bot_classes)

        # Layout: board on the left, sidebar on the right
        tile = max(12, min(32, 700 // max(config.width, config.height)))
        board_w, board_h = config.width * tile, config.height * tile
        self.sidebar_x = MARGIN * 2 + board_w
        size = (self.sidebar_x + SIDEBAR_W + MARGIN, max(board_h + 2 * MARGIN, 760))
        self.screen = pygame.display.set_mode(size)
        pygame.display.set_caption("LeakBots Arena")
        self.clock = pygame.time.Clock()
        self.renderer = BoardRenderer((MARGIN, MARGIN), tile)

        # UI state
        self.running = True
        self.playing = False
        self.ghost_pipes = True  # game-master toggle: buried pipes faintly visible to the audience
        self.show_marks = True
        self.speed = 0.34  # slider position (~8 moves/s)
        self._acc = 0.0
        self.selected_bot = 0
        self.card_rects: list[pygame.Rect] = []
        self._toast = ("", 0.0)

        # Simulation (runs in a background thread)
        self._sim_thread: threading.Thread | None = None
        self.sim_progress = (0, 0)
        self.sim_result: SimulationResult | None = None
        self.sim_seed = 0
        self._sim_stop = False

        self.sprites = [sprite_path(cls) for cls in self.bot_classes]
        self.new_game(seed)
        self._build_widgets()

    # --- Game control -----------------------------------------------------------------------
    def new_game(self, seed: int | None = None) -> None:
        self.seed = seed if seed is not None else random.randrange(1_000_000)
        self.game = Game(self.bot_classes, self.config, self.seed)
        self.renderer.set_game(self.game)
        self.playing = False
        self._acc = 0.0

    def restart(self) -> None:
        self.new_game(self.seed)

    def toggle_play(self) -> None:
        if self.game.over:
            return
        self.playing = not self.playing

    def step_once(self) -> None:
        if not self.game.over:
            self.playing = False
            self._step(time.perf_counter())

    @property
    def is_max_speed(self) -> bool:
        return self.speed >= 0.99

    @property
    def rate(self) -> float:
        """Moves per second chosen with the slider (log scale, 1 .. MAX_RATE)."""
        return MAX_RATE**self.speed

    def _step(self, now: float) -> None:
        event = self.game.step()
        if event is not None:
            move_time = 0.0 if self.is_max_speed or self.rate > 30 else min(0.18, 0.8 / self.rate)
            self.renderer.on_turn(event, now, move_time)

    def update(self, dt: float, now: float) -> None:
        """Advance the game according to play state and speed."""
        if self.playing and not self.game.over:
            start = time.perf_counter()
            if self.is_max_speed:
                while not self.game.over and time.perf_counter() - start < FRAME_STEP_BUDGET:
                    self._step(now)
            else:
                self._acc += dt * self.rate
                n, self._acc = int(self._acc), self._acc - int(self._acc)
                for _ in range(n):
                    if self.game.over or time.perf_counter() - start > 0.02:
                        self._acc = 0.0  # can't keep up: drop the backlog instead of lagging
                        break
                    self._step(now)
        if self.game.over:
            self.playing = False

    # --- Simulation -------------------------------------------------------------------------
    @property
    def simulating(self) -> bool:
        return self._sim_thread is not None and self._sim_thread.is_alive()

    def toggle_simulation(self) -> None:
        if self.simulating:
            self._sim_stop = True
            return
        self._sim_stop = False
        self.sim_result = None
        self.sim_seed = random.randrange(1_000_000)
        self.sim_progress = (0, self.sim_games)
        classes, config, n, seed = list(self.bot_classes), self.config, self.sim_games, self.sim_seed

        def work() -> None:
            def progress(done: int, total: int) -> None:
                self.sim_progress = (done, total)

            self.sim_result = run_games(classes, n, seed, config, progress, lambda: self._sim_stop)

        self._sim_thread = threading.Thread(target=work, daemon=True)
        self._sim_thread.start()

    # --- Sprites ----------------------------------------------------------------------------
    def handle_drop(self, file: str, bot_id: int | None = None) -> None:
        """Save a dropped PNG as the sprite of the bot under the cursor (or the selected bot)."""
        src = Path(file)
        if src.suffix.lower() != ".png":
            self.toast("Only .png files can be used as sprites")
            return
        try:
            pygame.image.load(str(src))
        except (pygame.error, FileNotFoundError):
            self.toast("Could not read that image")
            return
        if bot_id is None:
            bot_id = self._card_at(pygame.mouse.get_pos())
            bot_id = self.selected_bot if bot_id is None else bot_id
        cls = self.bot_classes[bot_id]
        SPRITES_DIR.mkdir(parents=True, exist_ok=True)
        dest = SPRITES_DIR / (cls.sprite or f"{cls.__name__}.png")
        if src.resolve() != dest.resolve():
            shutil.copyfile(src, dest)
        self.renderer.forget_sprites()
        self.sprites = [sprite_path(c) for c in self.bot_classes]
        self.toast(f"Sprite saved for {cls.display_name()}")

    def toast(self, message: str) -> None:
        self._toast = (message, time.perf_counter() + 3.0)

    def _card_at(self, pos: tuple[int, int]) -> int | None:
        for i, rect in enumerate(self.card_rects):
            if rect.collidepoint(pos):
                return i
        return None

    # --- Widgets & events -------------------------------------------------------------------
    def _build_widgets(self) -> None:
        x, w, gap, h = self.sidebar_x, SIDEBAR_W, 8, 36
        w3 = (w - 2 * gap) // 3
        w2 = (w - gap) // 2
        y1, y2, y3 = 150, 194, 238

        def r(col: int, row_y: int, width: int) -> pygame.Rect:
            return pygame.Rect(x + col * (width + gap), row_y, width, h)

        self.buttons = [
            Button(r(0, y1, w3), lambda: "PAUSE" if self.playing else "PLAY", self.toggle_play,
                   enabled=lambda: not self.game.over),
            Button(r(1, y1, w3), "STEP", self.step_once, enabled=lambda: not self.game.over),
            Button(r(2, y1, w3), "RESTART", self.restart),
            Button(r(0, y2, w2), "NEW MAP", self.new_game),
            Button(r(1, y2, w2), lambda: "CANCEL SIM" if self.simulating else f"SIMULATE {self.sim_games}",
                   self.toggle_simulation, accent=theme.GOOD),
            Button(r(0, y3, w2), lambda: f"PIPES: {'GHOST' if self.ghost_pipes else 'HIDDEN'}",
                   self._toggle_pipes, accent=theme.WATER),
            Button(r(1, y3, w2), lambda: f"MARKS: {'ON' if self.show_marks else 'OFF'}",
                   self._toggle_marks, accent=theme.WATER),
        ]
        self.slider = Slider(pygame.Rect(x + 4, 312, w - 8, 18), self.speed, self._set_speed)

    def _toggle_pipes(self) -> None:
        self.ghost_pipes = not self.ghost_pipes

    def _toggle_marks(self) -> None:
        self.show_marks = not self.show_marks

    def _set_speed(self, value: float) -> None:
        self.speed = value

    def handle_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.QUIT:
            self.running = False
        elif event.type == pygame.DROPFILE:
            self.handle_drop(event.file)
        elif event.type == pygame.KEYDOWN:
            keys = {
                pygame.K_SPACE: self.toggle_play,
                pygame.K_s: self.step_once,
                pygame.K_r: self.restart,
                pygame.K_n: self.new_game,
                pygame.K_m: self.toggle_simulation,
                pygame.K_p: self._toggle_pipes,
                pygame.K_d: self._toggle_marks,
                pygame.K_UP: lambda: self.slider.nudge(0.05),
                pygame.K_DOWN: lambda: self.slider.nudge(-0.05),
            }
            if event.key == pygame.K_ESCAPE:
                self.running = False
            elif event.key in keys:
                keys[event.key]()
        elif event.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP, pygame.MOUSEMOTION):
            if self.slider.handle(event):
                return
            for button in self.buttons:
                if button.handle(event):
                    return
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                card = self._card_at(event.pos)
                if card is not None:
                    self.selected_bot = card

    # --- Main loop --------------------------------------------------------------------------
    def run(self) -> None:
        while self.running:
            dt = self.clock.tick(60) / 1000
            for event in pygame.event.get():
                self.handle_event(event)
            now = time.perf_counter()
            self.update(dt, now)
            self.draw(now)
            pygame.display.flip()
        self._sim_stop = True
        pygame.quit()

    # --- Drawing ----------------------------------------------------------------------------
    def draw(self, now: float) -> None:
        self.screen.fill(theme.BG)
        self.renderer.draw(self.screen, now, self.ghost_pipes, self.show_marks, self.sprites)
        if self.game.over:
            self._draw_game_over()
        self._draw_sidebar(now)

    def _text(self, text: str, pos: tuple[int, int], size: int = 13, color=theme.TEXT, bold=False, right=False):
        img = theme.font(size, bold).render(text, True, color)
        rect = img.get_rect(topright=pos) if right else img.get_rect(topleft=pos)
        self.screen.blit(img, rect)
        return rect

    def _mode_label(self) -> str:
        n = len(self.bot_classes)
        return "SINGLE PLAYER" if n == 1 else "1 VS 1" if n == 2 else f"BATTLE ROYALE {n}"

    def _draw_sidebar(self, now: float) -> None:
        x, w = self.sidebar_x, SIDEBAR_W
        g = self.game
        # Title with drop shadow
        self._text("LEAKBOTS", (x + 3, MARGIN + 3), 34, (6, 6, 18), bold=True)
        self._text("LEAKBOTS", (x, MARGIN), 34, theme.ACCENT, bold=True)
        self._text("ARENA  ·  find and fix the leaks", (x + 2, MARGIN + 42), 12, theme.MUTED)

        # Status box
        box = pygame.Rect(x, 88, w, 52)
        pygame.draw.rect(self.screen, theme.PANEL, box, border_radius=6)
        pygame.draw.rect(self.screen, theme.PANEL_EDGE, box, width=2, border_radius=6)
        rnd = min(g.round + 1, g.config.max_rounds)
        self._text(f"ROUND {rnd}/{g.config.max_rounds}", (x + 10, 96), 14, bold=True)
        self._text(self._mode_label(), (x + w - 10, 97), 12, theme.ACCENT, right=True)
        found, fixed, total = g.leak_counts()
        self._text(f"LEAKS found {found}  fixed {fixed} / {total}", (x + 10, 118), 12, theme.WATER)
        self._text(f"seed {self.seed}", (x + w - 10, 119), 12, theme.MUTED, right=True)

        for button in self.buttons:
            button.draw(self.screen)
        speed = "MAX" if self.is_max_speed else f"{self.rate:.0f} moves/s"
        self._text("SPEED", (x, 284), 12, theme.MUTED, bold=True)
        self._text(speed, (x + w, 284), 12, theme.TEXT, right=True)
        self.slider.draw(self.screen)

        # Scoreboard + simulation panel share the remaining height
        top = 340
        hint_top = self.screen.get_height() - 58
        sim_h = 150 if len(self.bot_classes) <= 2 else 190
        self._text("SCOREBOARD", (x, top), 12, theme.MUTED, bold=True)
        card_area = hint_top - sim_h - (top + 20)
        card_h = max(24, min(84, card_area // len(self.bot_classes) - 6))
        self.card_rects = []
        y = top + 20
        for bot_id in range(len(self.bot_classes)):
            rect = pygame.Rect(x, y, w, card_h)
            self.card_rects.append(rect)
            self._draw_card(rect, bot_id)
            y += card_h + 6
        self._draw_sim_panel(pygame.Rect(x, hint_top - sim_h, w, sim_h - 8))

        # Hints / toast
        msg, until = self._toast
        if msg and now < until:
            self._text(msg, (x, hint_top + 4), 12, theme.GOOD, bold=True)
        else:
            self._text("Drag a PNG onto a bot card to set its sprite", (x, hint_top + 4), 11, theme.MUTED)
        self._text("SPACE play  S step  R restart  N new map", (x, hint_top + 22), 11, theme.MUTED)
        self._text("P pipes  D marks  M simulate  UP/DOWN speed", (x, hint_top + 36), 11, theme.MUTED)

    def _draw_card(self, rect: pygame.Rect, bot_id: int) -> None:
        g = self.game
        st = g.states[bot_id]
        color = theme.BOT_COLORS[bot_id % len(theme.BOT_COLORS)]
        active = not g.over and g.current_bot_id == bot_id and len(self.bot_classes) > 1
        pygame.draw.rect(self.screen, theme.PANEL, rect, border_radius=6)
        edge = color if active else theme.ACCENT if bot_id == self.selected_bot else theme.PANEL_EDGE
        pygame.draw.rect(self.screen, edge, rect, width=2, border_radius=6)

        # Portrait: uploaded sprite or the default robot
        portrait = min(rect.h - 12, 44)
        pr = pygame.Rect(rect.x + 8, rect.y + (rect.h - portrait) // 2, portrait, portrait)
        pygame.draw.rect(self.screen, (16, 18, 40), pr, border_radius=4)
        img = self.renderer.sprite(self.sprites[bot_id], portrait - 4)
        if img is not None:
            self.screen.blit(img, img.get_rect(center=pr.center))
        else:
            draw_robot(self.screen, pr.center, portrait - 4, color, g.states[bot_id].facing)

        tx = pr.right + 10
        holes = st.open_holes if g.over else g.open_pipe_holes(bot_id)
        self._text(self.labels[bot_id], (tx, rect.y + 6), 14, color, bold=True)
        self._text(str(st.score), (rect.right - 10, rect.y + 4), 22 if rect.h >= 40 else 16, theme.TEXT,
                   bold=True, right=True)
        if rect.h >= 44:
            self._text(f"pipes {st.pipes_detected}  found {st.leaks_found}  fixed {st.leaks_fixed}",
                       (tx, rect.y + 26), 12, theme.TEXT)
        if rect.h >= 66:
            pen = holes * g.config.penalty_open_pipe_hole
            tail = "" if g.over else " at end"
            line = f"holes {holes} (-{pen}{tail})  invalid {st.invalid}"
            self._text(line, (tx, rect.y + 44), 12, theme.MUTED)
            if st.last_error:
                self._text(f"error: {st.last_error}"[:40], (tx, rect.y + 60), 11, theme.DANGER)

    def _draw_sim_panel(self, rect: pygame.Rect) -> None:
        pygame.draw.rect(self.screen, theme.PANEL, rect, border_radius=6)
        pygame.draw.rect(self.screen, theme.PANEL_EDGE, rect, width=2, border_radius=6)
        x, y = rect.x + 10, rect.y + 8
        self._text("SIMULATION", (x, y), 12, theme.GOOD, bold=True)
        if self.simulating:
            done, total = self.sim_progress
            draw_progress(self.screen, pygame.Rect(x, y + 24, rect.w - 20, 16), done / max(1, total))
            self._text(f"{done}/{total} games  (seeds {self.sim_seed}+)", (x, y + 46), 12, theme.TEXT)
            return
        res = self.sim_result
        if res is None:
            self._text(f"Plays {self.sim_games} headless games with", (x, y + 24), 12, theme.MUTED)
            self._text("the current bots on fresh maps.", (x, y + 40), 12, theme.MUTED)
            return
        head = f"{res.games} games  {res.seconds:.1f}s  seed {res.first_seed}"
        self._text(head + ("  (cancelled)" if res.cancelled else ""), (x + 92, y + 1), 11, theme.MUTED)
        y += 22
        multi = len(res.seats) > 1
        compact = len(res.seats) > 2
        for bot_id, seat in enumerate(res.seats):
            if not seat.scores:
                continue
            color = theme.BOT_COLORS[bot_id % len(theme.BOT_COLORS)]
            self._text(seat.label, (x, y), 12, color, bold=True)
            win = f"  wins {100 * seat.wins / res.games:.0f}%" if multi else ""
            self._text(f"avg {seat.mean('scores'):.1f}{win}", (rect.right - 10, y), 12, theme.TEXT, bold=True,
                       right=True)
            y += 16
            if compact:
                continue
            self._text(f"min {min(seat.scores)}  max {max(seat.scores)}  invalid {seat.mean('invalid'):.0f}",
                       (x, y), 12, theme.MUTED)
            y += 16
            self._text(
                f"pipes {seat.mean('pipes_detected'):.1f}  found {seat.mean('leaks_found'):.2f}  "
                f"fixed {seat.mean('leaks_fixed'):.2f}  holes {seat.mean('open_holes'):.1f}",
                (x, y), 12, theme.MUTED,
            )
            y += 20

    def _draw_game_over(self) -> None:
        g = self.game
        board = self.renderer.board_rect()
        banner = pygame.Rect(0, 0, int(board.w * 0.78), 118)
        banner.center = board.center
        shade = pygame.Surface(banner.size, pygame.SRCALPHA)
        shade.fill((8, 8, 24, 225))
        self.screen.blit(shade, banner)
        pygame.draw.rect(self.screen, theme.ACCENT, banner, width=3, border_radius=6)
        title = theme.font(30, bold=True).render("GAME OVER", True, theme.ACCENT)
        self.screen.blit(title, title.get_rect(center=(banner.centerx, banner.y + 30)))
        if len(self.bot_classes) == 1:
            line = f"Final score: {g.states[0].score}"
        else:
            winners = g.winners()
            names = ", ".join(self.labels[i] for i in winners)
            line = f"{'Tie' if len(winners) > 1 else 'Winner'}: {names} ({g.states[winners[0]].score})"
        sub = theme.font(16, bold=True).render(line, True, theme.TEXT)
        self.screen.blit(sub, sub.get_rect(center=(banner.centerx, banner.y + 66)))
        hint = theme.font(12).render("RESTART replays this map  ·  NEW MAP rolls a new one", True, theme.MUTED)
        self.screen.blit(hint, hint.get_rect(center=(banner.centerx, banner.y + 96)))
