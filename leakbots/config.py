"""Central game settings. Change values here (or pass a custom GameConfig), not in the engine.

Terms: a *move* is one action by one bot; a *round* is one whole game, in which every bot
makes ``max_moves`` moves (bots take turns, one move at a time).
"""

from __future__ import annotations

from dataclasses import dataclass

from .model import PowerUp


@dataclass(frozen=True)
class GameConfig:
    """All tunable rules of a round. Frozen so a running round can never change its rules."""

    # Arena
    width: int = 25
    height: int = 25

    # Pipe network
    pipe_coverage: float = 0.28  # target fraction of cells that hold pipe
    n_leaks: int = 5  # broken pipe cells placed at the start of a round

    # Round length: every bot makes this many moves
    max_moves: int = 500

    # What bots may see
    reveal_pipe_dirs: bool = True  # unearthed pipes show which way they run

    # Scoring
    points_pipe_detected: int = 1  # discovering a never-unearthed pipe square (once per square per bot)
    points_leak_found: int = 5  # unearthing a broken pipe (once per leak)
    points_leak_fixed: int = 15  # fixing a broken pipe
    penalty_open_pipe_hole: int = 2  # per pipe cell still unearthed at the end, charged to digger

    # Power-ups (bought with points; buying uses a move; each once per bot)
    cost_ut: int = 15  # detect also reports leaks
    cost_satellite: int = 25  # Tenchijin satellite: whole pipe network with A-E risk grades
    # Risk grades: P(k) ~ risk_decay**k for k = 0..3 (0.3 -> 70.6 / 21.2 / 6.4 / 1.9 %).
    # Intact pipes get A, B, C, D for k = 0..3; leaks get E, D, C, B.
    risk_decay: float = 0.3

    def power_up_cost(self, power_up: PowerUp) -> int:
        return {PowerUp.UT: self.cost_ut, PowerUp.SATELLITE: self.cost_satellite}[power_up]

    def __post_init__(self) -> None:
        # Fail fast on configurations the engine cannot play
        if self.width < 3 or self.height < 3:
            raise ValueError("Grid must be at least 3x3")
        if not 0.0 < self.pipe_coverage < 1.0:
            raise ValueError("pipe_coverage must be between 0 and 1")
        if self.n_leaks < 0 or self.max_moves < 1:
            raise ValueError("n_leaks must be >= 0 and max_moves >= 1")
        if self.cost_ut < 0 or self.cost_satellite < 0:
            raise ValueError("power-up costs must be >= 0")
        if not 0.0 < self.risk_decay <= 1.0:
            raise ValueError("risk_decay must be in (0, 1]")


DEFAULT_CONFIG = GameConfig()
