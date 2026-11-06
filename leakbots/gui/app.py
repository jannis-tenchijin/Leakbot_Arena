"""Pygame front end: live board, controls, scoreboard, setup, rulebook and the simulation.

Runs on the desktop and in the browser (pygbag/WebAssembly). Browser constraints: the main
loop must be async and yield every frame, and there are no threads, so the simulation is
spread over frames instead of running in the background.
"""

from __future__ import annotations

import asyncio
import random
import shutil
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path

import pygame

from ..bot_api import BaseBot
from ..config import DEFAULT_CONFIG, GameConfig
from ..game import Game
from ..loader import SPRITES_DIR, bot_spec, discover_bots, sprite_path
from ..model import RISK_GRADES, Action, Dir, PowerUp
from ..simulation import SimulationResult, SimulationRunner, seat_labels
from . import theme
from .i18n import LANGS, rulebook, tr
from .manual import GAME_KEYS, HELP_ROWS, HumanBot, ManualControls
from .render import BoardRenderer, draw_robot
from .setup import MODES, GameSetup
from .widgets import Button, Slider, Switch, draw_progress, wrap_text

WEB = sys.platform == "emscripten"  # running in a browser via pygbag
MARGIN = 24
SIDEBAR_W = 340
MAX_RATE = 500  # moves per second at the right end of the slider (before "MAX")
FRAME_STEP_BUDGET = 0.012  # seconds per frame the engine may use in MAX mode
SIM_FRAME_BUDGET = 0.02  # seconds per frame spent on simulated rounds
MANUAL_MOVE_TIME = 0.12  # seconds the human's bot glides to the next square
HELP_KEY_W = 84  # key-cap column of the manual-mode help box


class App:
    """The arena window. ``run()`` blocks until the window is closed."""

    def __init__(
        self,
        bot_classes: Sequence[type[BaseBot]],
        config: GameConfig = DEFAULT_CONFIG,
        seed: int | None = None,
        sim_rounds: int = 100,
        lang: str = "en",
        manual: bool = False,
    ) -> None:
        pygame.init()
        theme.reset_fonts()
        self.base_bots = list(bot_classes)  # the line-up MANUAL hands back to
        self.manual = manual  # a human plays the only seat with the keyboard
        # Every bot in bots/ is always loaded, so SETUP can put any of them in any seat
        found, self.failed_bots = discover_bots()
        self.setup = GameSetup(found or self.base_bots, self.base_bots, manual)
        self.show_setup = False
        self._setup_hits: list[tuple[pygame.Rect, Callable[[], None]]] = []
        self.controls = ManualControls()
        self.config = config
        self.sim_rounds = sim_rounds
        self.set_language(lang if lang in LANGS else "en")

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
        self.show_rules = False
        self.speed = 0.34  # slider position (~8 moves/s)
        self._acc = 0.0
        self.selected_bot = 0
        self.card_rects: list[pygame.Rect] = []
        self._toast = ("", 0.0, theme.GOOD)

        # Simulation (a few rounds per frame, see SIM_FRAME_BUDGET)
        self.sim: SimulationRunner | None = None
        self.sim_result: SimulationResult | None = None

        self._set_lineup()
        self.new_game(seed)
        self._build_widgets()
        if self.failed_bots:
            self.toast(self.t("toast_bots_failed", files=", ".join(self.failed_bots)), theme.DANGER)

    def _set_lineup(self) -> None:
        """Seats for the next round: the human alone in manual mode, else the start line-up."""
        self.bot_classes: list[type[BaseBot]] = [HumanBot] if self.manual else list(self.base_bots)
        self.sprites = [sprite_path(cls) for cls in self.bot_classes]
        self.selected_bot = 0

    @property
    def labels(self) -> list[str]:
        """Seat names; the human's follows the UI language."""
        return [self.t("you")] if self.manual else seat_labels(self.bot_classes)

    # --- Language -------------------------------------------------------------------------
    def set_language(self, lang: str) -> None:
        self.lang = lang
        theme.set_language(lang)

    def toggle_language(self) -> None:
        self.set_language("ja" if self.lang == "en" else "en")

    def t(self, key: str, **values: object) -> str:
        """Text in the current language."""
        return tr(self.lang, key, **values)

    # --- Game control -----------------------------------------------------------------------
    def new_game(self, seed: int | None = None) -> None:
        self._punk_announced = False
        self.seed = seed if seed is not None else random.randrange(1_000_000)
        self.game = Game(self.bot_classes, self.config, self.seed)
        self.renderer.set_game(self.game)
        self.playing = False
        self._acc = 0.0

    def restart(self) -> None:
        self.new_game(self.seed)

    def toggle_play(self) -> None:
        if self.game.over or self.manual:  # in manual mode a key press is the clock
            return
        self.playing = not self.playing

    def step_once(self) -> None:
        if not self.game.over and not self.manual:
            self.playing = False
            self._step(time.perf_counter())

    # --- Manual mode ------------------------------------------------------------------------
    def toggle_manual(self) -> None:
        """Hand the only seat to the keyboard, or back to the start line-up. Keeps the map."""
        if self.simulating:  # the key help box takes the simulation panel's place
            self.toggle_simulation()
        self.manual = not self.manual
        self.controls.reset()
        self._set_lineup()
        self.new_game(self.seed)

    def play_manual(self, action: Action) -> None:
        """One human move: queue the action on the HumanBot and step the game exactly once."""
        if not self.manual or self.game.over or self.show_rules:
            return
        self.game.bots[0].queued = action
        event = self.game.step()
        self.renderer.on_turn(event, time.perf_counter(), MANUAL_MOVE_TIME)
        if not event.result.success:  # engine reasons are English, like the card's error line
            self.toast(self.t("toast_invalid", e=event.result.message), theme.DANGER)

    def toggle_rules(self) -> None:
        self.show_rules = not self.show_rules
        self.show_setup = False

    # --- Setup ------------------------------------------------------------------------------
    def toggle_setup(self) -> None:
        """Open/close the SETUP overlay (mode + bot for every seat)."""
        self.show_setup = not self.show_setup
        self.show_rules = False

    def start_setup(self) -> None:
        """START: begin a round with the mode and bots picked in SETUP (same map)."""
        bots, manual = self.setup.lineup()
        if self.simulating:
            self.toggle_simulation()
        if not manual:
            self.base_bots = bots  # also what MANUAL hands back to
        self.manual = manual
        self.controls.reset()
        self.sim_result = None  # old statistics belonged to another line-up
        self._set_lineup()
        self.new_game(self.seed)
        self.show_setup = False

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

        if self.game.punk_pos is not None and not self._punk_announced:
            self._punk_announced = True
            self.toast(self.t("toast_punk"), theme.DANGER)

        # Simulated rounds share the frame with live play; slow frames (throttled tab) get more time
        if self.simulating:
            self.sim.advance(min(0.25, max(SIM_FRAME_BUDGET, dt * 0.5)))
            if self.sim.done:
                self.sim_result = self.sim.result()

    # --- Simulation -------------------------------------------------------------------------
    @property
    def simulating(self) -> bool:
        return self.sim is not None and not self.sim.done

    def toggle_simulation(self) -> None:
        if self.simulating:
            self.sim.cancel()
            self.sim_result = self.sim.result()
            return
        if self.manual:  # a human cannot be simulated
            return
        self.sim_result = None
        self.sim = SimulationRunner(self.bot_classes, self.sim_rounds, random.randrange(1_000_000), self.config)

    # --- Sprites ----------------------------------------------------------------------------
    def handle_drop(self, file: str, bot_id: int | None = None) -> None:
        """Save a dropped PNG as the sprite of the bot under the cursor (or the selected bot)."""
        src = Path(file)
        if src.suffix.lower() != ".png":
            self.toast(self.t("toast_png_only"))
            return
        try:
            pygame.image.load(str(src))
        except (pygame.error, FileNotFoundError):
            self.toast(self.t("toast_bad_image"))
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
        self.toast(self.t("toast_sprite_saved", name=cls.display_name()))

    def toast(self, message: str, color: tuple[int, int, int] = theme.GOOD) -> None:
        self._toast = (message, time.perf_counter() + 3.0, color)

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

        def auto() -> bool:  # bots play: the clock buttons work (in manual mode a key press is the clock)
            return not self.game.over and not self.manual

        setup_w = 100  # second row: SETUP | NEW MAP | RUN 100 ROUNDS
        # Third row: PIPES | MARKS | MANUAL, widths sized to the longest label of each (at size 12)
        pipes_w, marks_w = 120, 98
        manual_x = x + pipes_w + marks_w + 2 * gap
        self.manual_button = Button(pygame.Rect(manual_x, y3, x + w - manual_x, h),
                                    lambda: self.t("manual_on" if self.manual else "manual_off"),
                                    self.toggle_manual, size=12)
        self.buttons = [
            Button(r(0, y1, w3), lambda: self.t("pause" if self.playing else "play"), self.toggle_play,
                   enabled=auto),
            Button(r(1, y1, w3), lambda: self.t("step"), self.step_once, enabled=auto),
            Button(r(2, y1, w3), lambda: self.t("restart"), self.restart),
            Button(pygame.Rect(x, y2, setup_w, h), lambda: self.t("setup"), self.toggle_setup,
                   accent=theme.WATER, size=12),
            Button(pygame.Rect(x + setup_w + gap, y2, setup_w, h), lambda: self.t("new_map"), self.new_game,
                   size=12),
            Button(pygame.Rect(x + 2 * (setup_w + gap), y2, w - 2 * (setup_w + gap), h),
                   lambda: self.t("cancel_sim") if self.simulating else self.t("simulate", n=self.sim_rounds),
                   self.toggle_simulation, enabled=lambda: not self.manual, accent=theme.GOOD, size=12),
            Button(pygame.Rect(x, y3, pipes_w, h),
                   lambda: self.t("pipes_ghost" if self.ghost_pipes else "pipes_hidden"),
                   self._toggle_pipes, accent=theme.WATER, size=12),
            Button(pygame.Rect(x + pipes_w + gap, y3, marks_w, h),
                   lambda: self.t("marks_on" if self.show_marks else "marks_off"),
                   self._toggle_marks, accent=theme.WATER, size=12),
            self.manual_button,
        ]
        self.slider = Slider(pygame.Rect(x + 4, 312, w - 8, 18), self.speed, self._set_speed,
                             enabled=lambda: not self.manual)

        # Top-right corner: rulebook + language switch (both stay usable over the rulebook)
        right = x + w
        self.lang_switch = Switch(pygame.Rect(right - 96, MARGIN, 96, 28), [("en", "EN"), ("ja", "日本語")],
                                  lambda: self.lang, self.set_language)
        self.rules_button = Button(pygame.Rect(right - 96 - 6 - 64, MARGIN, 64, 28), lambda: self.t("rules"),
                                   self.toggle_rules, accent=theme.TEXT, size=12)
        self.close_rules_button = Button(pygame.Rect(right - 96 - 8 - 96, MARGIN, 96, 28),
                                         lambda: self.t("close"), self.toggle_rules, accent=theme.DANGER)
        self.close_setup_button = Button(pygame.Rect(right - 96 - 8 - 96, MARGIN, 96, 28),
                                         lambda: self.t("close"), self.toggle_setup, accent=theme.DANGER)

        # Largest logo size that stays clear of the corner buttons (fonts differ per platform)
        room = self.rules_button.rect.x - x - 8
        self.logo_size = 30
        while self.logo_size > 18 and theme.font(self.logo_size, True, japanese=False).size("LEAKBOTS")[0] > room:
            self.logo_size -= 1

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
            self._handle_key(event.key)
        elif event.type == pygame.KEYUP:
            self.controls.key_up(event.key)  # in every mode, so no modifier stays stuck after a switch
        elif event.type == pygame.WINDOWFOCUSLOST:
            self.controls.reset()  # keys released outside the window never send KEYUP
        elif event.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP, pygame.MOUSEMOTION):
            self._handle_mouse(event)

    def _handle_key(self, key: int) -> None:
        if self.show_setup:  # the overlay owns the keyboard: Esc/B close, Enter starts, L language
            setup_keys = {pygame.K_ESCAPE: self.toggle_setup, pygame.K_b: self.toggle_setup,
                          pygame.K_RETURN: self.start_setup, pygame.K_KP_ENTER: self.start_setup,
                          pygame.K_l: self.toggle_language}
            if key in setup_keys:
                setup_keys[key]()
            return
        if key == pygame.K_ESCAPE:
            if self.show_rules:
                self.show_rules = False
            elif not WEB:  # closing a browser tab is the web "quit"
                self.running = False
            return
        if self.manual and key in GAME_KEYS:
            # W A S D Q E R F 1 2 belong to the game here, so S/R/D never step, restart or toggle marks
            action = self.controls.key_down(key)  # E/R/F are tracked even under the rulebook
            if action is not None:
                self.play_manual(action)  # ignored while the rulebook is open
            return
        keys = {
            pygame.K_h: self.toggle_rules,
            pygame.K_l: self.toggle_language,
            pygame.K_b: self.toggle_setup,
        }
        if self.manual and not self.show_rules:  # no play/step/sim/speed keys: a key press is the clock
            keys.update({
                pygame.K_n: self.new_game,
                pygame.K_p: self._toggle_pipes,
            })
        elif not self.show_rules:  # game controls are paused while the rulebook is open
            keys.update({
                pygame.K_SPACE: self.toggle_play,
                pygame.K_s: self.step_once,
                pygame.K_r: self.restart,
                pygame.K_n: self.new_game,
                pygame.K_m: self.toggle_simulation,
                pygame.K_p: self._toggle_pipes,
                pygame.K_d: self._toggle_marks,
                pygame.K_UP: lambda: self.slider.nudge(0.05),
                pygame.K_DOWN: lambda: self.slider.nudge(-0.05),
            })
        if key in keys:
            keys[key]()

    def _handle_mouse(self, event: pygame.event.Event) -> None:
        if self.lang_switch.handle(event):
            return
        if self.show_setup:
            if self.close_setup_button.handle(event):
                return
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                for rect, action in self._setup_hits:
                    if rect.collidepoint(event.pos):
                        action()
                        return
            if event.type == pygame.MOUSEBUTTONUP and not self._rules_panel().collidepoint(event.pos):
                self.show_setup = False  # click outside the panel closes it
            return
        if self.show_rules:
            # Only the close button and the language switch work over the rulebook
            if self.close_rules_button.handle(event):
                return
            if event.type == pygame.MOUSEBUTTONUP and not self._rules_panel().collidepoint(event.pos):
                self.show_rules = False  # click outside the panel closes it
            return
        if self.rules_button.handle(event) or self.slider.handle(event):
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
        """Desktop entry: block until the window closes."""
        asyncio.run(self.run_async())

    async def run_async(self) -> None:
        """Frame loop. Yields to the event loop every frame, which the browser build requires."""
        while self.running:
            dt = self.clock.tick(60) / 1000
            for event in pygame.event.get():
                self.handle_event(event)
            now = time.perf_counter()
            self.update(dt, now)
            self.draw(now)
            pygame.display.flip()
            await asyncio.sleep(0)
        pygame.quit()

    # --- Drawing ----------------------------------------------------------------------------
    def draw(self, now: float) -> None:
        self.screen.fill(theme.BG)
        self.renderer.draw(self.screen, now, self.ghost_pipes, self.show_marks, self.sprites)
        if self.renderer.show_risk(self.ghost_pipes):
            self._draw_risk_legend()
        if self.game.over:
            self._draw_game_over()
        self._draw_sidebar(now)
        if self.show_rules:
            self._draw_rulebook()
        if self.show_setup:
            self._draw_setup()
        self.lang_switch.draw(self.screen)  # always on top, also over the rulebook

    def _text(self, text: str, pos: tuple[int, int], size: int = 13, color=theme.TEXT, bold=False, right=False):
        img = theme.font(size, bold).render(text, True, color)
        rect = img.get_rect(topright=pos) if right else img.get_rect(topleft=pos)
        self.screen.blit(img, rect)
        return rect

    def _mode_label(self) -> str:
        if self.manual:
            return self.t("mode_manual")
        n = len(self.bot_classes)
        return self.t("mode_single") if n == 1 else self.t("mode_duel") if n == 2 else self.t("mode_royale", n=n)

    def _draw_sidebar(self, now: float) -> None:
        x, w = self.sidebar_x, SIDEBAR_W
        g = self.game
        # Title with drop shadow (the logo stays English in both languages)
        title = theme.font(self.logo_size, bold=True, japanese=False)
        self.screen.blit(title.render("LEAKBOTS", True, (6, 6, 18)), (x + 3, MARGIN + 3))
        self.screen.blit(title.render("LEAKBOTS", True, theme.ACCENT), (x, MARGIN))
        self._text(self.t("subtitle"), (x + 2, MARGIN + 42), 12, theme.MUTED)
        self.rules_button.draw(self.screen)

        # Status box
        box = pygame.Rect(x, 88, w, 52)
        pygame.draw.rect(self.screen, theme.PANEL, box, border_radius=6)
        pygame.draw.rect(self.screen, theme.PANEL_EDGE, box, width=2, border_radius=6)
        move = min(g.move + 1, g.config.max_moves)
        self._text(self.t("move", m=move, max=g.config.max_moves), (x + 10, 96), 14, bold=True)
        self._text(self._mode_label(), (x + w - 10, 97), 12, theme.ACCENT, right=True)
        found, fixed, total = g.leak_counts()
        self._text(self.t("leaks", f=found, x=fixed, t=total), (x + 10, 118), 12, theme.WATER)
        self._text(self.t("seed", s=self.seed), (x + w - 10, 119), 12, theme.MUTED, right=True)

        for button in self.buttons:
            button.draw(self.screen)
        if self.manual:
            speed = self.t("speed_manual")
        else:
            speed = self.t("max") if self.is_max_speed else self.t("rate", n=f"{self.rate:.0f}")
        self._text(self.t("speed"), (x, 284), 12, theme.MUTED, bold=True)
        self._text(speed, (x + w, 284), 12, theme.TEXT, right=True)
        self.slider.draw(self.screen)

        # Scoreboard + simulation panel (key help in manual mode) share the remaining height
        top = 340
        hint_top = self.screen.get_height() - 58
        sim_h = 182 if self.manual else 150 if len(self.bot_classes) <= 2 else 190
        self._text(self.t("scoreboard"), (x, top), 12, theme.MUTED, bold=True)
        card_area = hint_top - sim_h - (top + 20)
        card_h = max(24, min(84, card_area // len(self.bot_classes) - 6))
        self.card_rects = []
        y = top + 20
        for bot_id in range(len(self.bot_classes)):
            rect = pygame.Rect(x, y, w, card_h)
            self.card_rects.append(rect)
            self._draw_card(rect, bot_id)
            y += card_h + 6
        panel = pygame.Rect(x, hint_top - sim_h, w, sim_h - 8)
        if self.manual:
            self._draw_manual_help(panel)
        else:
            self._draw_sim_panel(panel)

        # Hints / toast
        msg, until, color = self._toast
        if msg and now < until:
            # Largest size that fits: engine reasons of invalid moves can be long
            size = next((s for s in (12, 11, 10) if theme.font(s, True).size(msg)[0] <= w), 9)
            self._text(msg, (x, hint_top + 4), size, color, bold=True)
        else:
            self._text(self.t("hint_web_sprite" if WEB else "hint_drop"), (x, hint_top + 4), 11, theme.MUTED)
        hints = ("hint_manual1", "hint_manual2") if self.manual else ("hint_keys1", "hint_keys2")
        self._text(self.t(hints[0]), (x, hint_top + 22), 11, theme.MUTED)
        self._text(self.t(hints[1]), (x, hint_top + 36), 11, theme.MUTED)

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
            draw_robot(self.screen, pr.center, portrait - 4, color, g.states[bot_id].facing,
                       PowerUp.PUNK in st.upgrades)

        tx = pr.right + 10
        holes = st.open_holes if g.over else g.open_pipe_holes(bot_id)
        name = self._text(self.labels[bot_id], (tx, rect.y + 6), 14, color, bold=True)
        self._draw_badges(st.upgrades, name.right + 6, rect.y + 6, st.chained_left)
        self._text(str(st.score), (rect.right - 10, rect.y + 4), 22 if rect.h >= 40 else 16, theme.TEXT,
                   bold=True, right=True)
        if rect.h >= 44:
            self._text(self.t("card_counts", p=st.pipes_detected, f=st.leaks_found, x=st.leaks_fixed),
                       (tx, rect.y + 26), 12, theme.TEXT)
        if rect.h >= 66:
            pen = holes * g.config.penalty_open_pipe_hole
            key = "card_holes_final" if g.over else "card_holes_live"
            self._text(self.t(key, h=holes, pen=pen, i=st.invalid), (tx, rect.y + 44), 12, theme.MUTED)
            if st.last_error:
                self._text(self.t("error", e=st.last_error)[:40], (tx, rect.y + 60), 11, theme.DANGER)

    def _draw_badges(self, upgrades, x: int, y: int, chained: int = 0) -> None:
        """Small pills next to a bot's name: power-ups, punk, and a ball-and-chain countdown."""
        pills = [(self.t(key), color) for power_up, key, color in (
            (PowerUp.UT, "badge_ut", theme.WATER),
            (PowerUp.SATELLITE, "badge_sat", theme.ACCENT),
            (PowerUp.PUNK, "badge_punk", theme.DANGER),
        ) if power_up in upgrades]
        if chained:
            pills.append((self.t("badge_chained", n=chained), theme.MUTED))
        for text, color in pills:
            img = theme.font(10, bold=True).render(text, True, (14, 16, 38))
            pill = img.get_rect(topleft=(x + 5, y + 2)).inflate(10, 4)
            pygame.draw.rect(self.screen, color, pill, border_radius=7)
            self.screen.blit(img, img.get_rect(center=pill.center))
            x = pill.right + 3

    def _draw_manual_help(self, rect: pygame.Rect) -> None:
        """Key help box (replaces the simulation panel in manual mode); the held modifier row lights up."""
        pygame.draw.rect(self.screen, theme.PANEL, rect, border_radius=6)
        pygame.draw.rect(self.screen, theme.PANEL_EDGE, rect, width=2, border_radius=6)
        x, y = rect.x + 10, rect.y + 8
        self._text(self.t("help_title"), (x, y), 12, theme.ACCENT, bold=True)
        y += 22
        costs = {"help_buy_ut": self.config.cost_ut, "help_buy_sat": self.config.cost_satellite}
        for keys_key, text_key, modifier in HELP_ROWS:
            if modifier is not None and modifier == self.controls.modifier:
                pygame.draw.rect(self.screen, (52, 56, 112), (x - 5, y - 1, rect.w - 10, 17), border_radius=4)
            self._text(self.t(keys_key), (x, y), 12, theme.ACCENT, bold=True)
            # Only the buy rows have a {c} placeholder; format() ignores it for the others
            self._text(self.t(text_key, c=costs.get(text_key)), (x + HELP_KEY_W, y), 12, theme.TEXT)
            y += 17
        self._text(self.t("help_note"), (x, y + 4), 11, theme.MUTED)
        self.help_bottom = y + 4 + theme.font(11).get_linesize()  # checked by the layout test

    def _draw_sim_panel(self, rect: pygame.Rect) -> None:
        pygame.draw.rect(self.screen, theme.PANEL, rect, border_radius=6)
        pygame.draw.rect(self.screen, theme.PANEL_EDGE, rect, width=2, border_radius=6)
        x, y = rect.x + 10, rect.y + 8
        title = self._text(self.t("sim_title"), (x, y), 12, theme.GOOD, bold=True)
        if self.simulating:
            done, total = self.sim.played, self.sim.n_rounds
            draw_progress(self.screen, pygame.Rect(x, y + 24, rect.w - 20, 16), done / max(1, total))
            self._text(self.t("sim_progress", d=done, t=total, s=self.sim.seed), (x, y + 46), 12, theme.TEXT)
            return
        res = self.sim_result
        if res is None:
            for i, line in enumerate(self.t("sim_idle", n=self.sim_rounds).split("\n")):
                self._text(line, (x, y + 24 + 16 * i), 12, theme.MUTED)
            return
        head = self.t("sim_head", g=res.rounds, sec=f"{res.seconds:.1f}", s=res.first_seed)
        if res.cancelled:
            head += "  " + self.t("sim_cancelled")
        self._text(head, (title.right + 12, y + 1), 11, theme.MUTED)
        y += 22
        multi = len(res.seats) > 1
        compact = len(res.seats) > 2
        for bot_id, seat in enumerate(res.seats):
            if not seat.scores:
                continue
            color = theme.BOT_COLORS[bot_id % len(theme.BOT_COLORS)]
            self._text(seat.label, (x, y), 12, color, bold=True)
            line = self.t("sim_avg", a=f"{seat.mean('scores'):.1f}")
            if multi:
                line += "  " + self.t("sim_wins", w=f"{100 * seat.wins / res.rounds:.0f}")
            self._text(line, (rect.right - 10, y), 12, theme.TEXT, bold=True, right=True)
            y += 16
            if compact:
                continue
            self._text(self.t("sim_minmax", lo=min(seat.scores), hi=max(seat.scores),
                              i=f"{seat.mean('invalid'):.0f}"), (x, y), 12, theme.MUTED)
            y += 16
            self._text(self.t("sim_counts", p=f"{seat.mean('pipes_detected'):.1f}",
                              f=f"{seat.mean('leaks_found'):.2f}", x=f"{seat.mean('leaks_fixed'):.2f}",
                              h=f"{seat.mean('open_holes'):.1f}"), (x, y), 12, theme.MUTED)
            y += 20

    def _draw_risk_legend(self) -> pygame.Rect:
        """One-line key for the satellite risk letters, in the free strip below the board."""
        board = self.renderer.board_rect()
        label = theme.font(12, bold=True).render(self.t("risk_legend"), True, theme.TEXT)
        badges = [self.renderer.risk_badge(grade) for grade in RISK_GRADES]
        h = max(label.get_height(), badges[0].get_height()) + 6
        w = label.get_width() + sum(b.get_width() + 3 for b in badges) + 26
        box = pygame.Rect(board.x - 4, board.bottom + 8, w, h)  # board frame ends 4 px below the board
        pygame.draw.rect(self.screen, theme.PANEL, box, border_radius=6)
        pygame.draw.rect(self.screen, theme.PANEL_EDGE, box, width=2, border_radius=6)
        self.screen.blit(label, label.get_rect(midleft=(box.x + 10, box.centery)))
        x = box.x + 10 + label.get_width() + 10
        for badge in badges:  # the A-E colour scale, same chips as on the board
            self.screen.blit(badge, badge.get_rect(midleft=(x, box.centery)))
            x += badge.get_width() + 3
        return box

    def _draw_game_over(self) -> None:
        g = self.game
        board = self.renderer.board_rect()
        banner = pygame.Rect(0, 0, int(board.w * 0.78), 118)
        banner.center = board.center
        shade = pygame.Surface(banner.size, pygame.SRCALPHA)
        shade.fill((8, 8, 24, 225))
        self.screen.blit(shade, banner)
        pygame.draw.rect(self.screen, theme.ACCENT, banner, width=3, border_radius=6)
        title = theme.font(30, bold=True).render(self.t("game_over"), True, theme.ACCENT)
        self.screen.blit(title, title.get_rect(center=(banner.centerx, banner.y + 30)))
        if len(self.bot_classes) == 1:
            line = self.t("final_score", s=g.states[0].score)
        else:
            winners = g.winners()
            names = ", ".join(self.labels[i] for i in winners)
            line = self.t("tie" if len(winners) > 1 else "winner", names=names, s=g.states[winners[0]].score)
        sub = theme.font(16, bold=True).render(line, True, theme.TEXT)
        self.screen.blit(sub, sub.get_rect(center=(banner.centerx, banner.y + 66)))
        hint = theme.font(12).render(self.t("game_over_hint"), True, theme.MUTED)
        self.screen.blit(hint, hint.get_rect(center=(banner.centerx, banner.y + 96)))

    # --- Rulebook ---------------------------------------------------------------------------
    def _rules_panel(self) -> pygame.Rect:
        w, h = self.screen.get_size()
        return pygame.Rect(MARGIN, MARGIN + 36, w - 2 * MARGIN, h - 2 * MARGIN - 36)

    def _draw_rulebook(self) -> None:
        shade = pygame.Surface(self.screen.get_size(), pygame.SRCALPHA)
        shade.fill((6, 6, 20, 225))
        self.screen.blit(shade, (0, 0))
        panel = self._rules_panel()
        pygame.draw.rect(self.screen, theme.PANEL, panel, border_radius=10)
        pygame.draw.rect(self.screen, theme.ACCENT, panel, width=3, border_radius=10)

        # Header row above the panel: title on the left, close + language on the right
        self._text(self.t("rulebook_title"), (MARGIN + 4, MARGIN + 2), 26, theme.ACCENT, bold=True)
        self.close_rules_button.draw(self.screen)

        pad, gutter = 22, 34
        col_w = (panel.w - 2 * pad - gutter) // 2
        columns = rulebook(self.lang, self.config)
        # Largest text size at which both columns fit inside the panel
        size = next(
            (s for s in (14, 13, 12) if all(
                self._draw_rule_column(c, 0, panel.y + 18, col_w, s, draw=False) <= panel.bottom - 8
                for c in columns)),
            11,
        )
        self.rules_text_size = size
        for col, sections in enumerate(columns):
            self._draw_rule_column(sections, panel.x + pad + col * (col_w + gutter), panel.y + 18, col_w, size)

    def _draw_rule_column(self, sections, x: int, y: int, width: int, size: int, draw: bool = True) -> int:
        """Draw (or with draw=False only measure) one rulebook column; returns the bottom y."""
        body = theme.font(size)
        bold = theme.font(size, bold=True)
        line_h = body.get_linesize() + 1
        for title, items in sections:
            if draw:
                self._text(title, (x, y), size + 2, theme.WATER, bold=True)
            y += size * 2
            label_w = max((bold.size(label)[0] for label, _ in items if label), default=0)
            for label, text in items:
                indent = label_w + 12 if label else 16  # bullets for plain notes, a column for labels
                lines = wrap_text(body, text, width - indent)
                if draw:
                    if label:
                        color = theme.GOOD if label.startswith("+") else theme.DANGER if label.startswith("-") \
                            else theme.ACCENT
                        self.screen.blit(bold.render(label, True, color), (x, y))
                    else:
                        pygame.draw.circle(self.screen, theme.MUTED, (x + 4, y + line_h // 2), 2)
                    for i, line in enumerate(lines):
                        self.screen.blit(body.render(line, True, theme.TEXT), (x + indent, y + i * line_h))
                y += line_h * len(lines) + 5
            y += 12
        return y

    # --- Setup overlay ----------------------------------------------------------------------
    def _draw_setup(self) -> None:
        """Mode tabs, one row per seat with < bot > arrows, +/- seats (battle royale), START."""
        shade = pygame.Surface(self.screen.get_size(), pygame.SRCALPHA)
        shade.fill((6, 6, 20, 225))
        self.screen.blit(shade, (0, 0))
        panel = self._rules_panel()
        pygame.draw.rect(self.screen, theme.PANEL, panel, border_radius=10)
        pygame.draw.rect(self.screen, theme.WATER, panel, width=3, border_radius=10)
        self._text(self.t("setup_title"), (MARGIN + 4, MARGIN + 2), 26, theme.WATER, bold=True)
        self.close_setup_button.draw(self.screen)

        setup = self.setup
        hits: list[tuple[pygame.Rect, Callable[[], None]]] = []

        def button(rect: pygame.Rect, label: str, action: Callable[[], None], accent=theme.ACCENT,
                   enabled: bool = True, size: int = 13) -> None:
            Button(rect, label, action, enabled=lambda: enabled, accent=accent, size=size).draw(self.screen)
            if enabled:
                hits.append((rect, action))

        pad = 22
        x0, y = panel.x + pad, panel.y + pad
        inner_w = panel.w - 2 * pad

        # Mode tabs
        tab_w = (inner_w - 3 * 10) // 4
        for i, mode in enumerate(MODES):
            rect = pygame.Rect(x0 + i * (tab_w + 10), y, tab_w, 40)
            if mode == setup.mode:
                pygame.draw.rect(self.screen, theme.ACCENT, rect, border_radius=6)
                img = theme.font(14, bold=True).render(self.t(f"tab_{mode}"), True, (14, 16, 38))
                self.screen.blit(img, img.get_rect(center=rect.center))
            else:
                button(rect, self.t(f"tab_{mode}"), lambda m=mode: setup.set_mode(m), size=14)
        y += 62

        if setup.mode == "manual":
            for i, line in enumerate(self.t("setup_manual").split("\n")):
                self._text(line, (x0, y + 26 * i), 16, theme.TEXT if i == 0 else theme.MUTED, bold=i == 0)
        else:
            self._text(self.t("setup_seats", n=len(setup.seats), total=len(setup.available)), (x0, y), 13,
                       theme.MUTED)
            y += 30
            name_w = 360
            for seat, cls in enumerate(setup.seats):
                color = theme.BOT_COLORS[seat % len(theme.BOT_COLORS)]
                row = pygame.Rect(x0, y, inner_w, 44)
                pygame.draw.rect(self.screen, (20, 22, 50), row, border_radius=6)
                icon = pygame.Rect(row.x + 8, row.y + 4, 36, 36)
                img = self.renderer.sprite(sprite_path(cls), 34)
                if img is not None:
                    self.screen.blit(img, img.get_rect(center=icon.center))
                else:
                    draw_robot(self.screen, icon.center, 34, color, Dir.RIGHT)
                self._text(self.t("seat", n=seat + 1), (icon.right + 12, row.y + 13), 14, color, bold=True)
                left = pygame.Rect(icon.right + 110, row.y + 5, 40, 34)
                name_box = pygame.Rect(left.right + 8, row.y + 5, name_w, 34)
                right = pygame.Rect(name_box.right + 8, row.y + 5, 40, 34)
                button(left, "<", lambda s=seat: setup.cycle(s, -1))
                pygame.draw.rect(self.screen, theme.PANEL, name_box, border_radius=6)
                pygame.draw.rect(self.screen, theme.PANEL_EDGE, name_box, width=2, border_radius=6)
                name = theme.font(15, bold=True).render(cls.display_name(), True, theme.TEXT)
                self.screen.blit(name, name.get_rect(midleft=(name_box.x + 12, name_box.centery)))
                spec = theme.font(11).render(bot_spec(cls), True, theme.MUTED)
                self.screen.blit(spec, spec.get_rect(midright=(name_box.right - 10, name_box.centery)))
                button(right, ">", lambda s=seat: setup.cycle(s, +1))
                y += 50
            if setup.mode == "royale":
                button(pygame.Rect(x0, y + 4, 120, 34), self.t("remove_seat"), setup.remove_seat,
                       enabled=setup.can_remove(), size=12)
                button(pygame.Rect(x0 + 130, y + 4, 120, 34), self.t("add_seat"), setup.add_seat,
                       enabled=setup.can_add(), size=12)

        # Footer: hint + START
        start = pygame.Rect(panel.right - pad - 240, panel.bottom - pad - 48, 240, 48)
        button(start, self.t("start_round"), self.start_setup, accent=theme.GOOD, size=16)
        self._text(self.t("setup_hint"), (x0, start.centery - 8), 12, theme.MUTED)
        self._setup_hits = hits
