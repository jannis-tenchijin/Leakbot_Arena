"""The game master: owns the true map, applies the rules, keeps score.

Terms: a *move* is one action by one bot (bots take turns); a *round* is one whole game
(one ``Game``), in which every bot makes ``config.max_moves`` moves.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType

from .bot_api import ActionResult, BaseBot, BotView, SatelliteReading
from .config import DEFAULT_CONFIG, GameConfig
from .model import (
    BUYABLE,
    PUNK_ITEM,
    Action,
    ActionType,
    Cell,
    Dir,
    PipeState,
    Pos,
    PowerUp,
    Surface,
    dirs_from_mask,
)
from .pipes import generate_pipe_masks, pick_leaks, pick_risk_grades


# ActionResult.message of two special successful moves (the GUI animates them)
MSG_DRAGGING = "dragging the ball and chain"
MSG_PUNK = "collected the punk upgrade"


@dataclass
class BotState:
    """Engine-side bookkeeping for one bot (position, score breakdown, private memory)."""

    pos: Pos
    score: int = 0
    pipes_detected: int = 0
    leaks_found: int = 0
    leaks_fixed: int = 0
    open_holes: int = 0  # pipe holes charged at the end of the round
    spent: int = 0  # points spent on power-ups
    bonus: int = 0  # points from pickups (punk upgrade)
    chain_used: bool = False  # punk: the one ball and chain of the round has been placed
    chained_left: int = 0  # moves this bot still drags a ball and chain (0 = free)
    dragging: Dir | None = None  # chained: direction of the step it has started dragging toward
    invalid: int = 0
    actions: int = 0
    last_scored_turn: int = -1
    facing: Dir = Dir.RIGHT  # direction of the last action (for drawing)
    last_result: ActionResult | None = None
    last_error: str | None = None
    upgrades: set[PowerUp] = field(default_factory=set)  # power-ups bought this round
    detected: dict[Pos, bool] = field(default_factory=dict)  # private memory: pos -> had a pipe
    detected_leaks: dict[Pos, bool] = field(default_factory=dict)  # UT readings: pos -> leaking
    discovered: set[Pos] = field(default_factory=set)  # squares that can no longer pay +1 to this bot
    satellite: dict[Pos, SatelliteReading] = field(default_factory=dict)  # every pipe square, once bought


@dataclass(frozen=True)
class MoveEvent:
    """What happened in one move; the GUI uses these for animations."""

    turn: int  # global sequence number of the move (0 = first move of the round)
    bot_id: int
    from_pos: Pos
    target: Pos | None
    action: Action | None
    result: ActionResult


class Game:
    """One round (one game) between one or more bots.

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
        # Satellite risk grades come from their own RNG so the map/spawn/bot sequences stay unchanged
        risk_rng = random.Random(f"risk-{self.seed}")
        for (x, y), grade in pick_risk_grades(masks, leaks, risk_rng, config.risk_decay).items():
            self._cells[y][x].risk = grade

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
            self._states.append(BotState(pos=tuple(pos)))
            self._cells[pos[1]][pos[0]].occupant = bot_id

        self.turn = 0  # total actions taken so far
        self.over = False
        self.punk_pos: Pos | None = None  # where the punk pickup lies (once it has appeared)
        self.punk_spawned = config.punk_spawn_move == 0  # 0 disables the pickup
        self.history: list[MoveEvent] = []
        # Auto-detect every spawn square before the hooks run, so bots already see it there
        for bot_id, state in enumerate(self._states):
            self._scan(bot_id, [state.pos])
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
    def move(self) -> int:
        """Moves each bot has completed so far (the current move number, 0-based)."""
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

    # --- Move loop -------------------------------------------------------------------------
    def run(self) -> Game:
        """Play until every bot has made max_moves moves."""
        while not self.over:
            self.step()
        return self

    def step(self) -> MoveEvent | None:
        """Let the bot whose turn it is make one move. Returns None once the round is over."""
        if self.over:
            return None
        if not self.punk_spawned and self.move + 1 >= self.config.punk_spawn_move:
            self._spawn_punk()  # appears at the start of move number punk_spawn_move
        bot_id = self.current_bot_id
        state = self._states[bot_id]
        from_pos = state.pos
        was_chained = state.chained_left > 0
        try:
            action = self.bots[bot_id].act(BotView(self, bot_id))
        except Exception as exc:  # a crashing bot only wastes its own move
            state.last_error = f"{type(exc).__name__}: {exc}"
            action = None
            result = ActionResult(None, False, f"bot error: {state.last_error}")
        else:
            result = self.apply_action(bot_id, action)

        state.actions += 1
        state.invalid += not result.success
        if was_chained:  # the ball and chain wears off after chain_duration of the victim's moves
            state.chained_left -= 1
            if state.chained_left == 0:
                state.dragging = None
        state.last_result = result
        # Only directional actions have a target square (DETECT scans around the bot, BUY has none)
        done = result.action
        aimed = done is not None and done.type not in (ActionType.DETECT, ActionType.BUY)
        target = done.direction.step(from_pos) if aimed else None
        event = MoveEvent(self.turn, bot_id, from_pos, target, result.action, result)
        self.history.append(event)

        self.turn += 1
        if self.move >= self.config.max_moves:
            self._finish()
        return event

    # --- Rules -----------------------------------------------------------------------------
    def apply_action(self, bot_id: int, action: object) -> ActionResult:
        """Validate and execute one action for a bot. Invalid actions change nothing."""
        if not (isinstance(action, Action) and isinstance(action.type, ActionType)):
            return ActionResult(None, False, f"not a valid Action: {action!r}")

        cfg = self.config
        state = self._states[bot_id]

        def fail(msg: str) -> ActionResult:
            return ActionResult(action, False, msg)

        # A chained bot's drag only survives a second MOVE in the same direction
        if not (action.type is ActionType.MOVE and action.direction is state.dragging):
            state.dragging = None

        if action.type is ActionType.BUY:
            return self._buy(bot_id, action)

        if action.type is ActionType.DETECT:
            # Always valid: scans the in-grid orthogonal neighbours; any direction is ignored
            around = [p for p in (d.step(state.pos) for d in Dir) if self.in_bounds(p)]
            points, scan, scan_leaks = self._scan(bot_id, around)
            return ActionResult(action, True, points=points, scan=scan, scan_leaks=scan_leaks)

        # Every other action targets the neighbouring square in a direction
        if not isinstance(action.direction, Dir):
            return ActionResult(None, False, f"{action.type.value} needs a Dir: {action!r}")
        state.facing = action.direction
        target = action.direction.step(state.pos)

        if not self.in_bounds(target):
            return fail("target is outside the grid")
        cell = self.cell(target)
        kind = action.type

        if kind is ActionType.MOVE:
            if cell.surface is Surface.UNEARTHED:
                return fail("cannot stand on an unearthed cell")
            if cell.occupant is not None:
                return fail("cell is occupied")
            if state.chained_left > 0 and state.dragging is not action.direction:
                # Ball and chain: this move only drags the ball; the next MOVE this way steps
                state.dragging = action.direction
                return ActionResult(action, True, message=MSG_DRAGGING)
            state.dragging = None
            self.cell(state.pos).occupant = None
            cell.occupant = bot_id
            state.pos = target
            # Free auto-detect of the square the bot just stepped onto
            points, scan, scan_leaks = self._scan(bot_id, [target])
            message = ""
            if cell.item == PUNK_ITEM:
                points += self._collect_punk(bot_id, cell)
                message = MSG_PUNK
            return ActionResult(action, True, message, points=points, scan=scan, scan_leaks=scan_leaks)

        if kind is ActionType.CHAIN:
            if PowerUp.PUNK not in state.upgrades:
                return fail("only a punk has a ball and chain")
            if state.chain_used:
                return fail("the ball and chain is already used this round")
            victim = cell.occupant
            if victim is None or victim == bot_id:
                return fail("no opponent on that square")
            if self._states[victim].chained_left > 0:
                return fail("that bot is already chained")
            self._states[victim].chained_left = cfg.chain_duration
            self._states[victim].dragging = None
            state.chain_used = True
            return ActionResult(action, True)

        if kind is ActionType.UNEARTH:
            if cell.surface is Surface.UNEARTHED:
                return fail("cell is already unearthed")
            if cell.occupant is not None:
                return fail("a bot is standing there")
            if cell.item is not None:
                return fail("an object lies there")
            cell.surface = Surface.UNEARTHED
            cell.revealed = True
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

    def _buy(self, bot_id: int, action: Action) -> ActionResult:
        """BUY: pay points for a power-up (uses the move; needs enough points; once per round)."""
        state = self._states[bot_id]
        power_up = action.power_up
        if not isinstance(power_up, PowerUp):
            return ActionResult(None, False, f"buy needs a PowerUp: {action!r}")
        if power_up not in BUYABLE:
            return ActionResult(action, False, f"{power_up.value} cannot be bought")
        cost = self.config.power_up_cost(power_up)
        if power_up in state.upgrades:
            return ActionResult(action, False, f"{power_up.value} already bought")
        if state.score < cost:
            return ActionResult(action, False, f"{power_up.value} costs {cost} points, you have {state.score}")
        state.score -= cost
        state.spent += cost
        state.upgrades.add(power_up)
        self._on_power_up(bot_id, power_up)
        return ActionResult(action, True, points=-cost)

    def _on_power_up(self, bot_id: int, power_up: PowerUp) -> None:
        """Apply one-off effects at the moment of purchase (UT works later, inside detect)."""
        if power_up is PowerUp.SATELLITE:
            # The buyer privately learns the whole network with risk grades. No points: every
            # square counts as discovered, so later detects cannot pay +1 for it either.
            state = self._states[bot_id]
            for y, row in enumerate(self._cells):
                for x, cell in enumerate(row):
                    pos = (x, y)
                    state.detected[pos] = cell.has_pipe
                    state.discovered.add(pos)
                    if cell.has_pipe:
                        state.satellite[pos] = SatelliteReading(dirs_from_mask(cell.pipe_mask), cell.risk)

    # --- Punk upgrade ----------------------------------------------------------------------
    def _spawn_punk(self) -> None:
        """Place the punk pickup on a random free, untouched square (own RNG keeps others intact)."""
        self.punk_spawned = True
        free = [
            (x, y)
            for y, row in enumerate(self._cells)
            for x, c in enumerate(row)
            if c.surface is Surface.UNTOUCHED and c.occupant is None and c.item is None
        ]
        if free:
            self.punk_pos = random.Random(f"punk-{self.seed}").choice(free)
            self.cell(self.punk_pos).item = PUNK_ITEM

    def _collect_punk(self, bot_id: int, cell: Cell) -> int:
        """Stepping onto the pickup: mohawk (PowerUp.PUNK), bonus points, one ball and chain."""
        state = self._states[bot_id]
        cell.item = None
        self.punk_pos = None
        state.upgrades.add(PowerUp.PUNK)
        state.bonus += self.config.points_punk
        self._award(state, self.config.points_punk)
        return self.config.points_punk

    # --- Internals -------------------------------------------------------------------------
    def _scan(
        self, bot_id: int, squares: Iterable[Pos]
    ) -> tuple[int, Mapping[Pos, bool], Mapping[Pos, bool] | None]:
        """Detect squares for a bot (DETECT and the free auto-detect share this).

        Records each square in the bot's private memory, pays for newly discovered pipes and,
        with UT, reads leaks. Returns (points, scan, scan_leaks) for the ActionResult.
        """
        state = self._states[bot_id]
        has_ut = PowerUp.UT in state.upgrades
        scan: dict[Pos, bool] = {}
        leaks: dict[Pos, bool] = {}
        points = 0
        for pos in squares:
            cell = self.cell(pos)
            scan[pos] = state.detected[pos] = cell.has_pipe
            # +1 only for a pipe square that is new to this bot: never detected by it before and
            # never unearthed by anyone (unearthed squares were seen by every bot; refilling
            # does not make a pipe "undiscovered")
            if cell.has_pipe and not cell.revealed and pos not in state.discovered:
                state.discovered.add(pos)
                state.pipes_detected += 1
                points += self.config.points_pipe_detected
            if has_ut:
                # UT reading: no points and no leak_found (unearth still pays +found, fix +fixed)
                leaks[pos] = state.detected_leaks[pos] = cell.pipe_state is PipeState.BROKEN
        self._award(state, points)
        return points, MappingProxyType(scan), MappingProxyType(leaks) if has_ut else None

    def _award(self, state: BotState, points: int) -> None:
        if points:
            state.score += points
            state.last_scored_turn = self.turn

    def _finish(self) -> None:
        # End-of-round penalty: every pipe hole still open costs its digger
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
