"""Run many headless rounds (one round = one game) and aggregate per-seat statistics."""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from .bot_api import BaseBot
from .config import DEFAULT_CONFIG, GameConfig
from .game import Game


@dataclass
class SeatStats:
    """Results of one seat (bot slot) across all simulated rounds."""

    label: str
    scores: list[int] = field(default_factory=list)
    pipes_detected: list[int] = field(default_factory=list)
    leaks_found: list[int] = field(default_factory=list)
    leaks_fixed: list[int] = field(default_factory=list)
    open_holes: list[int] = field(default_factory=list)
    spent: list[int] = field(default_factory=list)
    bonus: list[int] = field(default_factory=list)
    valves_reopened: list[int] = field(default_factory=list)
    closed_valves: list[int] = field(default_factory=list)
    invalid: list[int] = field(default_factory=list)
    wins: float = 0.0  # ties share the win

    def mean(self, attr: str) -> float:
        values = getattr(self, attr)
        # Plain mean: the browser build's trimmed stdlib has no `statistics` module
        return sum(values) / len(values) if values else 0.0


@dataclass
class SimulationResult:
    seats: list[SeatStats]
    rounds: int
    seconds: float
    first_seed: int | None
    cancelled: bool = False

    def summary_lines(self) -> list[str]:
        """Plain-text table used by the CLI."""
        lines = [f"{self.rounds} rounds in {self.seconds:.1f}s" + (" (cancelled)" if self.cancelled else "")]
        for s in self.seats:
            if not s.scores:
                continue
            # Win rate is meaningless with a single seat
            wins = f"  wins {100 * s.wins / self.rounds:.0f}%" if len(self.seats) > 1 else ""
            lines.append(f"{s.label}: avg {s.mean('scores'):.1f}  (min {min(s.scores)}, max {max(s.scores)}){wins}")
            lines.append(
                f"  pipes {s.mean('pipes_detected'):.1f}  found {s.mean('leaks_found'):.2f}  "
                f"fixed {s.mean('leaks_fixed'):.2f}  holes {s.mean('open_holes'):.1f}  "
                f"spent {s.mean('spent'):.1f}  invalid {s.mean('invalid'):.0f}"
            )
        return lines


def seat_labels(bot_classes: Sequence[type[BaseBot]]) -> list[str]:
    """Display names; repeated classes get a seat number, e.g. 'RandomBot #2'."""
    names = [cls.display_name() for cls in bot_classes]
    return [f"{n} #{i + 1}" if names.count(n) > 1 else n for i, n in enumerate(names)]


class SimulationRunner:
    """Plays a batch of rounds incrementally.

    The GUI calls ``advance(budget)`` once per frame, so a batch never freezes the window and
    no threads are needed (browsers running the WebAssembly build have none).
    Seeds are seed, seed+1, ... if a seed is given.
    """

    def __init__(
        self,
        bot_classes: Sequence[type[BaseBot]],
        n_rounds: int = 100,
        seed: int | None = None,
        config: GameConfig = DEFAULT_CONFIG,
    ) -> None:
        self.bot_classes = list(bot_classes)
        self.n_rounds = n_rounds
        self.seed = seed
        self.config = config
        self.seats = [SeatStats(label) for label in seat_labels(self.bot_classes)]
        self.played = 0
        self.cancelled = False
        self._start: float | None = None
        self._end: float | None = None

    @property
    def done(self) -> bool:
        return self.cancelled or self.played >= self.n_rounds

    def cancel(self) -> None:
        self.cancelled = True
        self._mark_end()

    def play_one(self) -> None:
        """Play the next round of the batch and record it."""
        if self.done:
            return
        if self._start is None:
            self._start = time.perf_counter()
        seed = None if self.seed is None else self.seed + self.played
        game = Game(self.bot_classes, self.config, seed).run()
        winners = game.winners()
        for bot_id, (seat, st) in enumerate(zip(self.seats, game.states)):
            seat.scores.append(st.score)
            seat.pipes_detected.append(st.pipes_detected)
            seat.leaks_found.append(st.leaks_found)
            seat.leaks_fixed.append(st.leaks_fixed)
            seat.open_holes.append(st.open_holes)
            seat.spent.append(st.spent)
            seat.bonus.append(st.bonus)
            seat.valves_reopened.append(st.valves_reopened)
            seat.closed_valves.append(st.closed_valves)
            seat.invalid.append(st.invalid)
            if bot_id in winners:
                seat.wins += 1 / len(winners)
        self.played += 1
        if self.done:
            self._mark_end()

    def advance(self, budget: float) -> None:
        """Play rounds for roughly ``budget`` seconds (always at least one round)."""
        start = time.perf_counter()
        self.play_one()
        while not self.done and time.perf_counter() - start < budget:
            self.play_one()

    def result(self) -> SimulationResult:
        start = self._start or time.perf_counter()
        end = self._end or time.perf_counter()
        return SimulationResult(self.seats, self.played, end - start, self.seed, cancelled=self.played < self.n_rounds)

    def _mark_end(self) -> None:
        if self._end is None:
            self._end = time.perf_counter()


def run_rounds(
    bot_classes: Sequence[type[BaseBot]],
    n_rounds: int = 100,
    seed: int | None = None,
    config: GameConfig = DEFAULT_CONFIG,
    progress: Callable[[int, int], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> SimulationResult:
    """Play n_rounds fresh rounds (new map each time) in one go and return the statistics."""
    runner = SimulationRunner(bot_classes, n_rounds, seed, config)
    while not runner.done:
        if should_stop and should_stop():
            runner.cancel()
            break
        runner.play_one()
        if progress:
            progress(runner.played, n_rounds)
    return runner.result()
