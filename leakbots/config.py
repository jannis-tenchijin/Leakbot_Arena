"""Central game settings. Change values here (or pass a custom GameConfig), not in the engine."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GameConfig:
    """All tunable rules of a match. Frozen so a running game can never change its rules."""

    # Arena
    width: int = 25
    height: int = 25

    # Pipe network
    pipe_coverage: float = 0.28  # target fraction of cells that hold pipe
    n_leaks: int = 5  # broken pipe cells placed at game start

    # Match length: one round = every bot has acted once
    max_rounds: int = 500

    # What bots may see
    reveal_pipe_dirs: bool = True  # unearthed pipes show which way they run
    ut_upgrade: bool = False  # future upgrade: detect also reports leaks

    # Scoring
    points_pipe_detected: int = 1  # detect on an untouched pipe cell (once per cell per bot)
    points_leak_found: int = 5  # unearthing a broken pipe (once per leak)
    points_leak_fixed: int = 15  # fixing a broken pipe
    penalty_open_pipe_hole: int = 2  # per pipe cell still unearthed at game end, charged to digger

    def __post_init__(self) -> None:
        # Fail fast on configurations the engine cannot play
        if self.width < 3 or self.height < 3:
            raise ValueError("Grid must be at least 3x3")
        if not 0.0 < self.pipe_coverage < 1.0:
            raise ValueError("pipe_coverage must be between 0 and 1")
        if self.n_leaks < 0 or self.max_rounds < 1:
            raise ValueError("n_leaks must be >= 0 and max_rounds >= 1")


DEFAULT_CONFIG = GameConfig()
