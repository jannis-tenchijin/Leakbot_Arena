"""The game master: owns the true map, applies the rules, keeps score."""

from __future__ import annotations

import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from .bot_api import ActionResult, BaseBot, BotView
from .config import DEFAULT_CONFIG, GameConfig
from .model import Action, ActionType, Cell, Dir, PipeState, Pos, Surface
from .pipes import generate_pipe_masks, pick_leaks


@dataclass
class BotState:
    """Engine-side bookkeeping for one bot (position, score breakdown, private memory)."""

    pos: Pos
    score: int = 0
    pipes_detected: int = 0
    leaks_found: int = 0
    leaks_fixed: int = 0
    open_holes: int = 0  # pipe holes charged at game end
    invalid: int = 0
    actions: int = 0
    last_scored_turn: int = -1
    ut_upgrade: bool = False
    facing: Dir = Dir.RIGHT  # direction of the last action (for drawing)
    last_result: ActionResult | None = None
    last_error: str | None = None
    detected: dict[Pos, bool] = field(default_factory=dict)
    detected_leaks: dict[Pos, bool] = field(default_factory=dict)
    scored_detects: set[Pos] = field(default_factory=set)


@dataclass(frozen=True)
class TurnEvent:
    """What happened in one turn; the GUI uses these for animations."""

    turn: int
    bot_id: int
    from_pos: Pos
    target: Pos | None
    action: Action | None
    result: ActionResult


class Game:
    """One match between one or more bots.

    Args:
        bot_classes: one BaseBot subclass per seat (the same class may appear twice).
        config: rules; defaults to DEFAULT_CONFIG.
        seed: makes the map, spawns and bot RNGs reproducible.
        masks / leaks / spawns: override the random layout (used by tests).
    """

    def __init__(
        self,
        bot_classes: Sequence[type[BaseBot]],
        config: GameConfig = DEFAULT_CONFIG,
        seed: int | None = None,
        *,
        masks: list[list[int]] | None = None,
        leaks: Sequence[Pos] | None = None,
        spawns: Sequence[Pos] | None = None,
    ) -> None:
        if not bot_classes:
            raise ValueError("A game needs at least one bot")
        self.config = config
        self.seed = seed if seed is not None else random.randrange(2**31)
        self.rng = random.Random(self.seed)

        # Map: pipe network + leaks
        if masks is None:
            masks = generate_pipe_masks(config.width, config.height, self.rng, config.pipe_coverage)
        if len(masks) != config.height or any(len(row) != config.width for row in masks):
            raise ValueError("masks must be height x width")
        self._cells = [
            [Cell(pipe_mask=m, pipe_state=PipeState.OK if m else None) for m in row] for row in masks
        ]
        if leaks is None:
            leaks = pick_leaks(masks, config.n_leaks, self.rng)
        for x, y in leaks:
            if not self._cells[y][x].has_pipe:
                raise ValueError(f"Leak at {(x, y)} is not on a pipe")
            self._cells[y][x].pipe_state = PipeState.BROKEN
        self.leak_positions: tuple[Pos, ...] = tuple(leaks)

        # Bots
        if spawns is None:
            spawns = self._pick_spawns(len(bot_classes))
        if len(spawns) != len(bot_classes):
            raise ValueError("Need exactly one spawn per bot")
        self.bots: list[BaseBot] = [cls() for cls in bot_classes]
        self._states: list[BotState] = []
        for bot_id, (bot, pos) in enumerate(zip(self.bots, spawns)):
            bot.id = bot_id
            bot.rng = random.Random(self.rng.randrange(2**31))  # per-bot reproducible randomness
            self._states.append(BotState(pos=tuple(pos), ut_upgrade=config.ut_upgrade))
            self._cells[pos[1]][pos[0]].occupant = bot_id

        self.turn = 0  # total actions taken so far
        self.over = False
        self.history: list[TurnEvent] = []
        for bot_id, bot in enumerate(self.bots):
            self._call_hook(bot_id, bot.on_game_start)

    # --- Read access for the GUI / simulation (game-master view, includes hidden truth) ---
    @property
    def cells(self) -> list[list[Cell]]:
        return self._cells

    @property
    def states(self) -> list[BotState]:
        return self._states

    @property
    def n_bots(self) -> int:
        return len(self.bots)

    @property
    def round(self) -> int:
        return self.turn // self.n_bots

    @property
    def current_bot_id(self) -> int:
        return self.turn % self.n_bots

    def in_bounds(self, pos: Pos) -> bool:
        return 0 <= pos[0] < self.config.width and 0 <= pos[1] < self.config.height

    def cell(self, pos: Pos) -> Cell:
        return self._cells[pos[1]][pos[0]]

    def leak_counts(self) -> tuple[int, int, int]:
        """(found, fixed, total) leaks on the map."""
        cells = [self.cell(p) for p in self.leak_positions]
        found = sum(c.leak_found for c in cells)
        fixed = sum(c.pipe_state is PipeState.FIXED for c in cells)
        return found, fixed, len(cells)

    def open_pipe_holes(self, bot_id: int) -> int:
        """Pipe cells currently left unearthed by this bot (what the end penalty will charge)."""
        return sum(
            1
            for row in self._cells
            for c in row
            if c.surface is Surface.UNEARTHED and c.has_pipe and c.dug_by == bot_id
        )

    def ranking(self) -> list[int]:
        """Bot ids, best first: score, then fewer invalid moves, then earlier last score."""
        return sorted(range(self.n_bots), key=self._rank_key)

    def _rank_key(self, bot_id: int) -> tuple[int, int, int]:
        s = self._states[bot_id]
        return (-s.score, s.invalid, s.last_scored_turn)

    def winners(self) -> list[int]:
        """All bots tied for first place under the full tie-break."""
        best = self._rank_key(self.ranking()[0])
        return [i for i in range(self.n_bots) if self._rank_key(i) == best]

    # --- Turn loop -------------------------------------------------------------------------
    def run(self) -> Game:
        """Play until the round cap."""
        while not self.over:
            self.step()
        return self

    def step(self) -> TurnEvent | None:
        """Let the current bot perform one action. Returns None once the game is over."""
        if self.over:
            return None
        bot_id = self.current_bot_id
        state = self._states[bot_id]
        from_pos = state.pos
        try:
            action = self.bots[bot_id].act(BotView(self, bot_id))
        except Exception as exc:  # a crashing bot only wastes its own turn
            state.last_error = f"{type(exc).__name__}: {exc}"
            action = None
            result = ActionResult(None, False, f"bot error: {state.last_error}")
        else:
            result = self.apply_action(bot_id, action)

        state.actions += 1
        state.invalid += not result.success
        state.last_result = result
        target = action.direction.step(from_pos) if result.action is not None else None
        event = TurnEvent(self.turn, bot_id, from_pos, target, result.action, result)
        self.history.append(event)

        self.turn += 1
        if self.round >= self.config.max_rounds:
            self._finish()
        return event

    # --- Rules -----------------------------------------------------------------------------
    def apply_action(self, bot_id: int, action: object) -> ActionResult:
        """Validate and execute one action for a bot. Invalid actions change nothing."""
        if not (
            isinstance(action, Action)
            and isinstance(action.type, ActionType)
            and isinstance(action.direction, Dir)
        ):
            return ActionResult(None, False, f"not a valid Action: {action!r}")

        cfg = self.config
        state = self._states[bot_id]
        state.facing = action.direction
        target = action.direction.step(state.pos)

        def fail(msg: str) -> ActionResult:
            return ActionResult(action, False, msg)

        if not self.in_bounds(target):
            return fail("target is outside the grid")
        cell = self.cell(target)
        kind = action.type

        if kind is ActionType.MOVE:
            if cell.surface is Surface.UNEARTHED:
                return fail("cannot stand on an unearthed cell")
            if cell.occupant is not None:
                return fail("cell is occupied")
            self.cell(state.pos).occupant = None
            cell.occupant = bot_id
            state.pos = target
            return ActionResult(action, True)

        if kind is ActionType.DETECT:
            points = 0
            has_pipe = cell.has_pipe
            state.detected[target] = has_pipe
            # +1 for each untouched pipe cell this bot detects for the first time
            if has_pipe and cell.surface is Surface.UNTOUCHED and target not in state.scored_detects:
                state.scored_detects.add(target)
                state.pipes_detected += 1
                points += cfg.points_pipe_detected
            leak = None
            if state.ut_upgrade:
                leak = cell.pipe_state is PipeState.BROKEN
                state.detected_leaks[target] = leak
                if leak and not cell.leak_found:
                    cell.leak_found = True
                    state.leaks_found += 1
                    points += cfg.points_leak_found
            self._award(state, points)
            return ActionResult(action, True, points=points, detected_pipe=has_pipe, detected_leak=leak)

        if kind is ActionType.UNEARTH:
            if cell.surface is Surface.UNEARTHED:
                return fail("cell is already unearthed")
            if cell.occupant is not None:
                return fail("a bot is standing there")
            cell.surface = Surface.UNEARTHED
            cell.dug_by = bot_id  # only the game master tracks diggers
            points = 0
            if cell.pipe_state is PipeState.BROKEN and not cell.leak_found:
                cell.leak_found = True
                state.leaks_found += 1
                points = cfg.points_leak_found
            self._award(state, points)
            return ActionResult(action, True, points=points)

        if kind is ActionType.FIX:
            if cell.surface is not Surface.UNEARTHED:
                return fail("unearth the cell before fixing")
            if not cell.has_pipe:
                return fail("there is no pipe here")
            if cell.pipe_state is not PipeState.BROKEN:
                return fail("pipe is not broken")
            cell.pipe_state = PipeState.FIXED
            state.leaks_fixed += 1
            self._award(state, cfg.points_leak_fixed)
            return ActionResult(action, True, points=cfg.points_leak_fixed)

        if kind is ActionType.REFILL:
            if cell.surface is not Surface.UNEARTHED:
                return fail("nothing to refill")
            if cell.pipe_state is PipeState.BROKEN:
                return fail("fix the leak before refilling")
            cell.surface = Surface.UNTOUCHED
            cell.dug_by = None
            return ActionResult(action, True)

        return fail(f"unknown action type {kind}")  # pragma: no cover

    # --- Internals -------------------------------------------------------------------------
    def _award(self, state: BotState, points: int) -> None:
        if points:
            state.score += points
            state.last_scored_turn = self.turn

    def _finish(self) -> None:
        # End-of-game penalty: every pipe hole still open costs its digger
        penalty = self.config.penalty_open_pipe_hole
        for row in self._cells:
            for c in row:
                if c.surface is Surface.UNEARTHED and c.has_pipe and c.dug_by is not None:
                    s = self._states[c.dug_by]
                    s.open_holes += 1
                    s.score -= penalty
        self.over = True
        for bot_id, bot in enumerate(self.bots):
            self._call_hook(bot_id, bot.on_game_end)

    def _call_hook(self, bot_id: int, hook: Callable[[BotView], None]) -> None:
        try:
            hook(BotView(self, bot_id))
        except Exception as exc:
            self._states[bot_id].last_error = f"{type(exc).__name__}: {exc}"

    def _pick_spawns(self, n: int) -> list[Pos]:
        """Random spawn cells, spread out so bots do not start next to each other."""
        w, h = self.config.width, self.config.height
        cells = [(x, y) for y in range(h) for x in range(w)]
        if n > len(cells):
            raise ValueError("More bots than cells")
        chosen = [self.rng.choice(cells)]
        while len(chosen) < n:
            free = [c for c in self.rng.sample(cells, min(80, len(cells))) if c not in chosen]
            if not free:
                free = [c for c in cells if c not in chosen]
            chosen.append(
                max(free, key=lambda c: min(abs(c[0] - o[0]) + abs(c[1] - o[1]) for o in chosen))
            )
        return chosen
