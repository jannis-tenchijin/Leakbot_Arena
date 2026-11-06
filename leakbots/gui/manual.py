"""Manual mode: a human plays a single-player round with the keyboard.

One key press that issues an action = one move = one ``Game.step()``. The App asks
``ManualControls`` which action a key means, queues it on the ``HumanBot`` and steps once.
Keys are read as key codes (``event.key``), never as text input, so a Japanese IME cannot
swallow them; held modifiers (E/R/F, valves V/C/O) are tracked with KEYDOWN/KEYUP (more reliable
in browsers than ``pygame.key.get_pressed``).
"""

from __future__ import annotations

from collections.abc import Callable

import pygame

from ..bot_api import BaseBot, BotView
from ..model import Action, Dir, PowerUp

DIR_KEYS = {pygame.K_w: Dir.UP, pygame.K_a: Dir.LEFT, pygame.K_s: Dir.DOWN, pygame.K_d: Dir.RIGHT}
# Hold one of these, then tap a direction key
MODIFIER_KEYS: dict[int, Callable[[Dir], Action]] = {
    pygame.K_e: Action.unearth,
    pygame.K_r: Action.fix,
    pygame.K_f: Action.refill,
    pygame.K_v: Action.install_valve,  # V = valve
    pygame.K_c: Action.close_valve,  # C = close
    pygame.K_o: Action.open_valve,  # O = open
}
BUY_KEYS = {
    pygame.K_1: PowerUp.UT,
    pygame.K_KP1: PowerUp.UT,
    pygame.K_2: PowerUp.SATELLITE,
    pygame.K_KP2: PowerUp.SATELLITE,
}
DETECT_KEY = pygame.K_q
GAME_KEYS = frozenset(DIR_KEYS) | frozenset(MODIFIER_KEYS) | frozenset(BUY_KEYS) | {DETECT_KEY}

# Rows of the key help box: (i18n key of the key caps, i18n key of the text, modifier it highlights)
HELP_ROWS: tuple[tuple[str, str, int | None], ...] = (
    ("keys_move", "help_move", None),
    ("keys_detect", "help_detect", None),
    ("keys_unearth", "help_unearth", pygame.K_e),
    ("keys_fix", "help_fix", pygame.K_r),
    ("keys_refill", "help_refill", pygame.K_f),
    ("keys_valve", "help_valve", pygame.K_v),
    ("keys_close", "help_close", pygame.K_c),
    ("keys_open", "help_open", pygame.K_o),
    ("keys_buy_ut", "help_buy_ut", None),
    ("keys_buy_sat", "help_buy_sat", None),
)


class HumanBot(BaseBot):
    """The seat a human plays. ``act`` returns the action the keyboard queued for this move."""

    name = "You"  # the GUI shows the translated name ("You" / "あなた")

    def __init__(self) -> None:
        super().__init__()
        self.queued: Action | None = None

    def act(self, view: BotView) -> Action:
        action, self.queued = self.queued, None
        if action is None:  # the App never steps without a queued action; a slip only wastes this move
            raise RuntimeError("no action queued for the human player")
        return action


class ManualControls:
    """Maps key presses to actions and remembers which modifier (E/R/F/V/C/O) is held."""

    def __init__(self) -> None:
        self.held: list[int] = []  # modifier keys currently held down, most recent last

    @property
    def modifier(self) -> int | None:
        """The held modifier that applies to the next direction key (the latest one pressed)."""
        return self.held[-1] if self.held else None

    def key_down(self, key: int) -> Action | None:
        """Action for a pressed key, or None (modifiers only change what WASD does)."""
        if key in MODIFIER_KEYS:
            if key not in self.held:
                self.held.append(key)
            return None
        if key in DIR_KEYS:
            direction = DIR_KEYS[key]
            mod = self.modifier
            return MODIFIER_KEYS[mod](direction) if mod is not None else Action.move(direction)
        if key == DETECT_KEY:
            return Action.detect()
        if key in BUY_KEYS:
            return Action.buy(BUY_KEYS[key])
        return None

    def key_up(self, key: int) -> None:
        if key in self.held:
            self.held.remove(key)

    def reset(self) -> None:
        """Forget held keys (focus lost, mode switched): their KEYUP may never arrive."""
        self.held.clear()
