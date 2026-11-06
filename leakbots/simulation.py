"""Run many headless games and aggregate per-seat statistics."""

from __future__ import annotations

import statistics
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from .bot_api import BaseBot
from .config import DEFAULT_CONFIG, GameConfig
from .game import Game


@dataclass
class SeatStats:
    """Results of one seat (bot slot) across all simulated games."""

    label: str
    scores: list[int] = field(default_factory=list)
    pipes_detected: list[int] = field(default_factory=list)
    leaks_found: list[int] = field(default_factory=list)
    leaks_fixed: list[int] = field(default_factory=list)
    open_holes: list[int] = field(default_factory=list)
    invalid: list[int] = field(default_factory=list)
    wins: float = 0.0  # ties share the win

    def mean(self, attr: str) -> float:
        values = getattr(self, attr)
        return statistics.fmean(values) if values else 0.0


@dataclass
class SimulationResult:
    seats: list[SeatStats]
    games: int
    seconds: float
    first_seed: int | None
    cancelled: bool = False

    def summary_lines(self) -> list[str]:
        """Plain-text table used by the CLI and the GUI results panel."""
        lines = [f"{self.games} games in {self.seconds:.1f}s" + (" (cancelled)" if self.cancelled else "")]
        for s in self.seats:
            if not s.scores:
                continue
            # Win rate is meaningless with a single seat
            wins = f"  wins {100 * s.wins / self.games:.0f}%" if len(self.seats) > 1 else ""
            lines.append(f"{s.label}: avg {s.mean('scores'):.1f}  (min {min(s.scores)}, max {max(s.scores)}){wins}")
            lines.append(
                f"  pipes {s.mean('pipes_detected'):.1f}  found {s.mean('leaks_found'):.2f}  "
                f"fixed {s.mean('leaks_fixed'):.2f}  holes {s.mean('open_holes'):.1f}  "
                f"invalid {s.mean('invalid'):.0f}"
            )
        return lines


def seat_labels(bot_classes: Sequence[type[BaseBot]]) -> list[str]:
    """Display names; repeated classes get a seat number, e.g. 'RandomBot #2'."""
    names = [cls.display_name() for cls in bot_classes]
    return [f"{n} #{i + 1}" if names.count(n) > 1 else n for i, n in enumerate(names)]


def run_games(
    bot_classes: Sequence[type[BaseBot]],
    n_games: int = 100,
    seed: int | None = None,
    config: GameConfig = DEFAULT_CONFIG,
    progress: Callable[[int, int], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> SimulationResult:
    """Play n_games fresh games (new map each time). Seeds are seed, seed+1, ... if given."""
    seats = [SeatStats(label) for label in seat_labels(bot_classes)]
    start = time.perf_counter()
    played = 0
    for i in range(n_games):
        if should_stop and should_stop():
            break
        game = Game(bot_classes, config, None if seed is None else seed + i).run()
        winners = game.winners()
        for bot_id, (seat, st) in enumerate(zip(seats, game.states)):
            seat.scores.append(st.score)
            seat.pipes_detected.append(st.pipes_detected)
            seat.leaks_found.append(st.leaks_found)
            seat.leaks_fixed.append(st.leaks_fixed)
            seat.open_holes.append(st.open_holes)
            seat.invalid.append(st.invalid)
            if bot_id in winners:
                seat.wins += 1 / len(winners)
        played += 1
        if progress:
            progress(played, n_games)
    return SimulationResult(seats, played, time.perf_counter() - start, seed, cancelled=played < n_games)
