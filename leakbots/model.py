"""Core value types shared by the engine, the bot API and the GUI."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

Pos = tuple[int, int]  # (x, y); x grows right, y grows down


class Dir(Enum):
    """The four grid directions every directional action uses."""

    UP = "up"
    DOWN = "down"
    LEFT = "left"
    RIGHT = "right"

    @property
    def delta(self) -> Pos:
        return _DELTAS[self]

    @property
    def opposite(self) -> Dir:
        return _OPPOSITES[self]

    @property
    def bit(self) -> int:
        """Bit used in a pipe connection mask."""
        return _BITS[self]

    def step(self, pos: Pos) -> Pos:
        """Neighbouring position in this direction (may be outside the grid)."""
        return pos[0] + self.delta[0], pos[1] + self.delta[1]

    def turn_left(self) -> Dir:
        return _LEFT_OF[self]

    def turn_right(self) -> Dir:
        return _LEFT_OF[self].opposite


_DELTAS = {Dir.UP: (0, -1), Dir.DOWN: (0, 1), Dir.LEFT: (-1, 0), Dir.RIGHT: (1, 0)}
_OPPOSITES = {Dir.UP: Dir.DOWN, Dir.DOWN: Dir.UP, Dir.LEFT: Dir.RIGHT, Dir.RIGHT: Dir.LEFT}
_BITS = {Dir.UP: 1, Dir.RIGHT: 2, Dir.DOWN: 4, Dir.LEFT: 8}
_LEFT_OF = {Dir.UP: Dir.LEFT, Dir.LEFT: Dir.DOWN, Dir.DOWN: Dir.RIGHT, Dir.RIGHT: Dir.UP}


def dirs_from_mask(mask: int) -> frozenset[Dir]:
    """Directions encoded in a pipe connection mask."""
    return frozenset(d for d in Dir if mask & d.bit)


class Surface(Enum):
    """What the ground of a cell looks like."""

    UNTOUCHED = "untouched"
    UNEARTHED = "unearthed"


class PipeState(Enum):
    """Condition of a pipe cell."""

    OK = "ok"
    BROKEN = "broken"
    FIXED = "fixed"


class ActionType(Enum):
    """Every action a bot can take with one move."""

    MOVE = "move"
    DETECT = "detect"
    UNEARTH = "unearth"
    FIX = "fix"
    REFILL = "refill"
    BUY = "buy"  # buy a power-up with points


class PowerUp(Enum):
    """Upgrades a bot can buy once per round."""

    UT = "ut"  # ultrasonic testing: detect also reports leaks
    SATELLITE = "satellite"  # Tenchijin satellite: the whole pipe network with risk grades


RISK_GRADES = ("A", "B", "C", "D", "E")  # satellite risk grades of pipe squares, lowest risk first


@dataclass(frozen=True)
class Action:
    """One bot move: an action type plus its target (a direction, or a power-up for BUY)."""

    type: ActionType
    direction: Dir | None = None
    power_up: PowerUp | None = None

    @classmethod
    def buy(cls, power_up: PowerUp) -> Action:
        return cls(ActionType.BUY, power_up=power_up)

    # Convenience constructors so bot code reads naturally: Action.move(Dir.UP)
    @classmethod
    def move(cls, direction: Dir) -> Action:
        return cls(ActionType.MOVE, direction)

    @classmethod
    def detect(cls, direction: Dir | None = None) -> Action:
        return cls(ActionType.DETECT, direction)

    @classmethod
    def unearth(cls, direction: Dir) -> Action:
        return cls(ActionType.UNEARTH, direction)

    @classmethod
    def fix(cls, direction: Dir) -> Action:
        return cls(ActionType.FIX, direction)

    @classmethod
    def refill(cls, direction: Dir) -> Action:
        return cls(ActionType.REFILL, direction)

    def __str__(self) -> str:
        target = self.power_up or self.direction
        return f"{self.type.value} {target.value}" if target else self.type.value


@dataclass(slots=True)
class Cell:
    """Engine-internal cell state. Bots never receive this object directly."""

    surface: Surface = Surface.UNTOUCHED
    pipe_mask: int = 0  # connection bits (see Dir.bit); 0 means no pipe
    pipe_state: PipeState | None = None
    risk: str | None = None  # satellite risk grade "A".."E" of a pipe square (fixed for the round)
    leak_found: bool = False  # a bot has already been credited for finding this leak
    revealed: bool = False  # was unearthed at some point: its pipe is public knowledge forever
    dug_by: int | None = None  # bot id of the latest digger (game master only)
    occupant: int | None = None  # bot id standing here

    @property
    def has_pipe(self) -> bool:
        return self.pipe_mask != 0
