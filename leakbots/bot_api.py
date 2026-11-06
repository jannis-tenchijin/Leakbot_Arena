"""Everything a competitor needs to write a bot.

A bot subclasses ``BaseBot`` and implements ``act(view) -> Action``, called once per move.
The ``view`` is a read-only window onto the round: the public map plus the bot's own private
memory. Terms: a *move* is one action by one bot; a *round* is one whole game in which every
bot makes ``view.max_moves`` moves.
Bots must only use the public methods/properties below (no underscore attributes).
"""

from __future__ import annotations

import random
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING

from .model import Action, Dir, PipeState, Pos, PowerUp, Surface, dirs_from_mask

if TYPE_CHECKING:  # pragma: no cover
    from .game import Game


@dataclass(frozen=True)
class CellInfo:
    """Public knowledge about one cell, identical for every bot."""

    pos: Pos
    surface: Surface
    has_pipe: bool | None  # None if never unearthed (unknown), else True/False
    pipe_state: PipeState | None  # OK / BROKEN / FIXED for revealed pipes, else None
    pipe_dirs: frozenset[Dir]  # directions a revealed pipe runs (empty if unknown)
    bot: int | None  # id of the bot standing here, if any
    revealed: bool = False  # unearthed now or earlier: stays known after a refill

    @property
    def walkable(self) -> bool:
        """True if a bot could move here right now."""
        return self.surface is Surface.UNTOUCHED and self.bot is None

    @property
    def refilled(self) -> bool:
        """Dug up earlier and filled back in (every bot can see this)."""
        return self.revealed and self.surface is Surface.UNTOUCHED


@dataclass(frozen=True)
class ActionResult:
    """Outcome of a bot's previous action (available as ``view.last_result``)."""

    action: Action | None
    success: bool
    message: str = ""  # reason when the action was invalid
    points: int = 0  # points earned by this action (negative for a power-up purchase)
    detected_pipe: bool | None = None  # detect: was there a pipe?
    detected_leak: bool | None = None  # detect with UT upgrade: was it leaking?


class BotView:
    """Read-only view of the game handed to ``BaseBot.act``. Cheap to create, nothing copied."""

    __slots__ = ("_game", "_bot_id")

    def __init__(self, game: Game, bot_id: int) -> None:
        self._game = game
        self._bot_id = bot_id

    # --- Identity & time -------------------------------------------------------------
    @property
    def bot_id(self) -> int:
        return self._bot_id

    @property
    def me(self) -> Pos:
        """Your current position (x, y)."""
        return self._game._states[self._bot_id].pos

    @property
    def move(self) -> int:
        """Your current move number, starting at 0 (moves you have already made)."""
        return self._game.move

    @property
    def max_moves(self) -> int:
        """Moves every bot makes in this round."""
        return self._game.config.max_moves

    @property
    def moves_left(self) -> int:
        """Your moves remaining, including the current one."""
        return self._game.config.max_moves - self._game.move

    @property
    def n_bots(self) -> int:
        return len(self._game._states)

    # --- Scoring -----------------------------------------------------------------------
    @property
    def score(self) -> int:
        """Your score so far (the open-hole penalty is only applied at the end of the round)."""
        return self._game._states[self._bot_id].score

    @property
    def scores(self) -> tuple[int, ...]:
        """Current score of every bot, indexed by bot id."""
        return tuple(s.score for s in self._game._states)

    @property
    def points(self) -> Mapping[str, int]:
        """Point values of the current rules: pipe_detected, leak_found, leak_fixed,
        open_hole_penalty, and the power-up prices ut_cost, satellite_cost."""
        c = self._game.config
        return MappingProxyType(
            {
                "pipe_detected": c.points_pipe_detected,
                "leak_found": c.points_leak_found,
                "leak_fixed": c.points_leak_fixed,
                "open_hole_penalty": c.penalty_open_pipe_hole,
                "ut_cost": c.cost_ut,
                "satellite_cost": c.cost_satellite,
            }
        )

    # --- Power-ups -----------------------------------------------------------------------
    @property
    def upgrades(self) -> frozenset[PowerUp]:
        """Power-ups you have bought this round."""
        return frozenset(self._game._states[self._bot_id].upgrades)

    def has(self, power_up: PowerUp) -> bool:
        return power_up in self._game._states[self._bot_id].upgrades

    def cost(self, power_up: PowerUp) -> int:
        """Price of a power-up in points (buying also uses your move)."""
        return self._game.config.power_up_cost(power_up)

    def upgrades_of(self, bot_id: int) -> frozenset[PowerUp]:
        """Power-ups another bot has bought (public: purchases are announced)."""
        return frozenset(self._game._states[bot_id].upgrades)

    # --- Map ---------------------------------------------------------------------------
    @property
    def width(self) -> int:
        return self._game.config.width

    @property
    def height(self) -> int:
        return self._game.config.height

    def in_bounds(self, pos: Pos) -> bool:
        return 0 <= pos[0] < self.width and 0 <= pos[1] < self.height

    def cell(self, pos: Pos) -> CellInfo:
        """Public info about the cell at pos (raises IndexError outside the grid)."""
        if not self.in_bounds(pos):
            raise IndexError(f"{pos} is outside the {self.width}x{self.height} grid")
        c = self._game._cells[pos[1]][pos[0]]
        if c.revealed:  # everyone saw this square dug open; a refill does not hide it again
            show_dirs = self._game.config.reveal_pipe_dirs
            return CellInfo(
                pos,
                c.surface,
                c.has_pipe,
                c.pipe_state,
                dirs_from_mask(c.pipe_mask) if show_dirs else frozenset(),
                c.occupant,
                revealed=True,
            )
        return CellInfo(pos, c.surface, None, None, frozenset(), c.occupant)

    def surface(self, pos: Pos) -> Surface:
        """Fast shortcut for ``cell(pos).surface``."""
        return self._game._cells[pos[1]][pos[0]].surface

    def is_walkable(self, pos: Pos) -> bool:
        """True if pos is inside the grid, untouched and free of bots."""
        if not self.in_bounds(pos):
            return False
        c = self._game._cells[pos[1]][pos[0]]
        return c.surface is Surface.UNTOUCHED and c.occupant is None

    def neighbor(self, direction: Dir, pos: Pos | None = None) -> Pos:
        """Position next to pos (default: you) in a direction. May be outside the grid."""
        return direction.step(self.me if pos is None else pos)

    def bot_positions(self) -> dict[int, Pos]:
        """Positions of all bots (including you), keyed by bot id."""
        return {i: s.pos for i, s in enumerate(self._game._states)}

    # --- Private memory ----------------------------------------------------------------
    @property
    def detected(self) -> Mapping[Pos, bool]:
        """Your own detect results: pos -> had a pipe. Other bots cannot see this."""
        return MappingProxyType(self._game._states[self._bot_id].detected)

    @property
    def detected_leaks(self) -> Mapping[Pos, bool]:
        """Leak readings (pos -> leaking) from detects made after buying UT."""
        return MappingProxyType(self._game._states[self._bot_id].detected_leaks)

    @property
    def last_result(self) -> ActionResult | None:
        """Outcome of your previous move (None before your first move)."""
        return self._game._states[self._bot_id].last_result


class BaseBot:
    """Parent class of every bot. Override ``act``; the other hooks are optional.

    Class attributes:
        name:   display name (defaults to the class name)
        sprite: PNG filename in ``bots/sprites/`` (defaults to ``<ClassName>.png`` if present)
    """

    name: str | None = None
    sprite: str | None = None

    def __init__(self) -> None:
        # Set by the engine before on_game_start: your id and a seeded RNG for reproducible rounds
        self.id: int = -1
        self.rng: random.Random = random.Random()

    @classmethod
    def display_name(cls) -> str:
        return cls.name or cls.__name__

    def on_game_start(self, view: BotView) -> None:
        """Called once at the start of a round, before the first move."""

    def act(self, view: BotView) -> Action:
        """Return the Action for this move."""
        raise NotImplementedError

    def on_game_end(self, view: BotView) -> None:
        """Called once after the last move of the round (scores are final)."""
