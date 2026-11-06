"""Game setup: pick a mode and a bot for every seat (state only; the App draws it).

Modes: single player (1 bot), 1 vs 1 (2 bots), battle royale (3-8 bots) and manual (the
human plays one bot). Every bot found in ``bots/`` can sit in any seat, the same bot twice
included. The picks of each mode are remembered while you switch tabs.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..bot_api import BaseBot

MODES = ("single", "duel", "royale", "manual")
ROYALE_MIN, ROYALE_MAX = 3, 8
# Strategy bots first when filling default seats, so a fresh setup shows something interesting
PREFERRED = ("GovernmentBot", "TenchijinBot", "RandomBot")


def mode_for(n_bots: int, manual: bool = False) -> str:
    """The mode a line-up of n bots belongs to."""
    if manual:
        return "manual"
    return "single" if n_bots == 1 else "duel" if n_bots == 2 else "royale"


class GameSetup:
    """Mode + seats being edited in the SETUP overlay."""

    def __init__(self, available: Sequence[type[BaseBot]], lineup: Sequence[type[BaseBot]], manual: bool) -> None:
        if not available:
            raise ValueError("no bots available")
        # Bots from the command line that are not in bots/ (e.g. test bots) stay selectable too
        self.available = list(available) + [b for b in dict.fromkeys(lineup) if b not in available]
        preferred = sorted(self.available, key=lambda c: (c.__name__ not in PREFERRED,
                                                         PREFERRED.index(c.__name__) if c.__name__ in PREFERRED else 0))
        self.picks: dict[str, list[type[BaseBot]]] = {
            "single": preferred[:1],
            "duel": [preferred[i % len(preferred)] for i in range(2)],
            "royale": [preferred[i % len(preferred)] for i in range(ROYALE_MIN)],
            "manual": [],
        }
        self.mode = mode_for(len(lineup), manual)
        if self.mode != "manual" and lineup:
            self.picks[self.mode] = list(lineup)  # start from what is on the board

    @property
    def seats(self) -> list[type[BaseBot]]:
        return self.picks[self.mode]

    def set_mode(self, mode: str) -> None:
        if mode not in MODES:
            raise ValueError(mode)
        self.mode = mode

    def cycle(self, seat: int, step: int) -> None:
        """Next (+1) or previous (-1) bot for a seat, wrapping around the list."""
        current = self.available.index(self.seats[seat])
        self.seats[seat] = self.available[(current + step) % len(self.available)]

    def can_add(self) -> bool:
        return self.mode == "royale" and len(self.seats) < ROYALE_MAX

    def can_remove(self) -> bool:
        return self.mode == "royale" and len(self.seats) > ROYALE_MIN

    def add_seat(self) -> None:
        if self.can_add():
            self.seats.append(self.available[len(self.seats) % len(self.available)])

    def remove_seat(self) -> None:
        if self.can_remove():
            self.seats.pop()

    def lineup(self) -> tuple[list[type[BaseBot]], bool]:
        """(bots, manual) for the round START begins."""
        return list(self.seats), self.mode == "manual"
