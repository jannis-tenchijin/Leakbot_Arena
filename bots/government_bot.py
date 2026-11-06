"""GovernmentBot: an example competitor that works like a careful public water utility.

Strategy:
    Phase 1  Find a pipe, then follow the pipe network with steps and detects. Every newly
             discovered pipe square pays +1, until the bot can afford the ultrasonic test.
    Buy      As soon as ``view.score >= view.cost(PowerUp.UT)``: buy UT (15 points).
    Phase 2  Keep scanning along the pipes. With UT every scan also reports leaks; a reported
             leak is dug up (+5), fixed (+15) and refilled straight away.
    Always   Fix broken pipes other bots left open (+15) and never leave an own hole open.
    Punk     From move 200 a punk pickup lies on the map: if it is close, the bot detours to
             collect it (+10, denied to the opponents). As a punk it puts its one ball and chain
             on the strongest adjacent opponent. When chained itself, every step costs two
             moves: it keeps dragging the same way and plans its jobs with the doubled cost.

How it chooses where to scan: every square worth scanning gets a value (how likely it holds
pipe, plus a leak bonus once UT is bought). A breadth-first search over walkable squares then
picks the step or detect with the best value per move. Buried pipes are invisible, so their
course is guessed from earlier detects: pipes run in long straight lines, never dead-end inside
the map, and form one connected network (so new pipe is only searched next to known pipe).

Run it:    python main.py --bots government_bot
Benchmark: python simulate.py --bots government_bot random_bot --rounds 100 --seed 1
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterator

from leakbots import Action, ActionType, BaseBot, BotView, Dir, PipeState, Pos, PowerUp, Surface

# Chance that an unscanned square holds pipe, given what the bot already knows
BASE_CHANCE = 0.2  # before the first pipe is found (roughly 28 % of all squares are pipe)
STRAIGHT_CHANCE = 0.8  # straight ahead of a pipe square with one known link: pipes run straight
BRANCH_CHANCE = 0.1  # beside a pipe square whose two links are known: only a side branch fits

# With UT: what scanning a pipe square for a leak is worth (5 leaks hide in ~180 pipe squares)
LEAK_CHECK_VALUE = 0.5

DIG_MOVES = 3  # unearth + fix + refill: never start a dig that cannot be refilled in time

# Punk pickup: +10 is about what 10-15 moves of scanning earn (well under one point per move,
# since most squares around the bot are already scanned by move 200). Taking it also denies it
# to the opponents (and their ball and chain), so a detour of up to 15 moves is worth it.
PUNK_RANGE = 15


class GovernmentBot(BaseBot):
    name = "GovernmentBot"

    def on_game_start(self, view: BotView) -> None:
        # In-grid neighbours of every square, computed once (Dir.step is slow in hot loops)
        self.around: dict[Pos, list[tuple[Dir, Pos]]] = {
            (x, y): [(d, d.step((x, y))) for d in Dir if view.in_bounds(d.step((x, y)))]
            for y in range(view.height)
            for x in range(view.width)
        }
        # Private memory for this round (the view does not say who dug which hole)
        self.pipes: set[Pos] = {pos for pos, has_pipe in view.detected.items() if has_pipe}
        self.settled: set[Pos] = set()  # known pipes with nothing left to learn around them
        self.leaks: set[Pos] = set()  # squares UT reported as leaking and not fixed yet
        self.my_holes: set[Pos] = set()  # holes this bot dug that are still open
        self.public: dict[Pos, bool] = {}  # squares anyone dug up: pos -> has pipe (everyone sees it)
        self.broken: set[Pos] = set()  # open holes with a leaking pipe: anyone may fix them
        self.digging: Pos | None = None  # target of the unearth this bot just tried

    def act(self, view: BotView) -> Action:
        self._remember(view)
        jobs = self._jobs(view)

        # 1. As a punk: chain the strongest adjacent opponent (an own open hole is finished first)
        action = self._chain(view)
        if action is not None:
            return action

        # 2. Work on a hole or reported leak right next to the bot
        action = self._work_next_to_me(view, jobs)
        if action is not None:
            return action

        # 3. Phase switch: buy the ultrasonic test as soon as it is affordable
        if not view.has(PowerUp.UT) and view.score >= view.cost(PowerUp.UT):
            return Action.buy(PowerUp.UT)

        # 4. Walk to the punk pickup if it is close (and nearer than the next job),
        #    else 5. walk to the nearest job, otherwise 6. scan the most promising squares
        work = self._path_to_work(view, jobs)
        punk = self._path_to_punk(view)
        if punk is not None and (work is None or punk[0] < work[0]):
            return Action.move(punk[1])
        if work is not None:
            return Action.move(work[1])
        return self._explore(view)

    # --- Memory ----------------------------------------------------------------------------
    def _remember(self, view: BotView) -> None:
        """Update private memory with the outcome of the previous move and the public map."""
        result = view.last_result
        if result is not None:
            for pos, has_pipe in (result.scan or {}).items():
                if has_pipe:
                    self.pipes.add(pos)
            for pos, leaking in (result.scan_leaks or {}).items():
                if leaking:
                    self.leaks.add(pos)
            if result.success and result.action.type is ActionType.UNEARTH and self.digging is not None:
                self.my_holes.add(self.digging)
        self.digging = None

        # Other bots dig too: read the public map (in single player only this bot digs)
        if view.n_bots > 1:
            self._read_public_map(view)

        # Forget holes that were filled again and leaks that were repaired (by anyone)
        self.my_holes = {pos for pos in self.my_holes if view.surface(pos) is Surface.UNEARTHED}
        self.leaks = {pos for pos in self.leaks if view.cell(pos).pipe_state is not PipeState.FIXED}

    def _read_public_map(self, view: BotView) -> None:
        """Note every open hole: its pipe is public, and a broken one can be fixed by anyone."""
        self.broken = set()
        for pos in self.around:
            if view.surface(pos) is Surface.UNEARTHED:
                info = view.cell(pos)
                self.public[pos] = bool(info.has_pipe)
                if info.has_pipe:
                    self.pipes.add(pos)
                if info.pipe_state is PipeState.BROKEN:
                    self.broken.add(pos)

    # --- Leaks and holes -------------------------------------------------------------------
    def _jobs(self, view: BotView) -> dict[Pos, str]:
        """Squares that need work now: "fix", "refill" or "dig" (a reported, untouched leak)."""
        jobs = {}
        for pos in self.my_holes | self.broken | self.leaks:
            info = view.cell(pos)
            if info.surface is Surface.UNEARTHED:
                if info.pipe_state is PipeState.BROKEN:
                    jobs[pos] = "fix"  # +15, whoever dug it
                elif pos in self.my_holes:
                    jobs[pos] = "refill"  # own hole: avoid the end-of-round penalty
            elif (
                pos in self.leaks
                and view.moves_left >= DIG_MOVES
                and info.bot in (None, view.bot_id)
                and info.item is None  # the punk pickup blocks digging until someone takes it
            ):
                jobs[pos] = "dig"  # if the bot stands on it, _path_to_work steps off first
        return jobs

    def _work_next_to_me(self, view: BotView, jobs: dict[Pos, str]) -> Action | None:
        for d, target in self.around[view.me]:
            kind = jobs.get(target)
            if kind == "fix":
                return Action.fix(d)
            if kind == "refill":
                return Action.refill(d)
            if kind == "dig":  # _jobs already checked that no other bot stands there
                self.digging = target
                return Action.unearth(d)
        return None

    def _path_to_work(self, view: BotView, jobs: dict[Pos, str]) -> tuple[int, Dir] | None:
        """(travel moves, first step) to the cheapest square next to a job that still fits."""
        if not jobs:
            return None

        def moves_after_arrival(pos: Pos) -> int | None:
            needs = [DIG_MOVES if jobs[nxt] == "dig" else 1 for _, nxt in self.around[pos] if nxt in jobs]
            return min(needs, default=None)

        return self._cheapest_path(view, moves_after_arrival, view.moves_left)

    # --- Punk upgrade ----------------------------------------------------------------------
    def _path_to_punk(self, view: BotView) -> tuple[int, Dir] | None:
        """(travel moves, first step) to the punk pickup if it lies within PUNK_RANGE moves."""
        target = view.punk_pos
        if target is None:
            return None
        return self._cheapest_path(view, lambda pos: 0 if pos == target else None, PUNK_RANGE)

    def _chain(self, view: BotView) -> Action | None:
        """Ball and chain on the strongest adjacent opponent that is not chained yet."""
        if not view.can_chain or self.my_holes:
            return None  # an own open hole (dig, fix, refill) is finished first
        at = {pos: bot_id for bot_id, pos in view.bot_positions().items() if bot_id != view.bot_id}
        targets = [
            (view.scores[at[nxt]], -at[nxt], d)  # highest score first, then the lowest bot id
            for d, nxt in self.around[view.me]
            if nxt in at and view.chained_moves(at[nxt]) == 0
        ]
        return Action.chain(max(targets)[2]) if targets else None

    # --- Exploration -----------------------------------------------------------------------
    def _explore(self, view: BotView) -> Action:
        """Step or detect where the expected scan value per move is highest."""
        values = self._scan_values(view)
        if not values:
            return Action.detect()  # nothing left to learn; detect is always valid
        # A detect scans the four neighbours of the square the bot stands on
        detect_gain: dict[Pos, float] = {}
        for pos, value in values.items():
            for _, nxt in self.around[pos]:
                detect_gain[nxt] = detect_gain.get(nxt, 0.0) + value
        # Best gain any single plan can reach: lets the search stop early
        top = max(values.get(pos, 0.0) + detect_gain.get(pos, 0.0) for pos in {*values, *detect_gain})

        best_rate, best = 0.0, Action.detect()
        for pos, dist, first in self._reachable(view):
            if dist and top / self._travel(view, dist, view.dragging) <= best_rate:
                break  # nothing farther away can beat the best plan (travel never shrinks)
            cost = self._travel(view, dist, first)  # moves to walk there (2 per step when chained)
            own = values.get(pos, 0.0) if dist else 0.0  # stepping onto pos auto-detects it
            if dist and own / cost > best_rate:
                best_rate, best = own / cost, Action.move(first)
            rate = (own + detect_gain.get(pos, 0.0)) / (cost + 1)  # walk to pos, then detect
            if rate > best_rate:
                best_rate, best = rate, Action.move(first) if dist else Action.detect()
        return best

    def _scan_values(self, view: BotView) -> dict[Pos, float]:
        """Value of scanning each square that is worth a scan (squares not listed are worth 0)."""
        has_ut = view.has(PowerUp.UT)
        bonus = LEAK_CHECK_VALUE if has_ut else 0.0  # phase 2: every scan is also a leak check
        detected, checked, public = view.detected, view.detected_leaks, self.public
        if not self.pipes:  # no pipe found yet: any unscanned square might hold one
            unscanned = (pos for pos in self.around if pos not in detected and pos not in public)
            return {pos: BASE_CHANCE * (1 + bonus) for pos in unscanned}

        values: dict[Pos, float] = {}
        for pipe in self.pipes - self.settled:
            leak_state_known = pipe in checked or pipe in public
            if has_ut and not leak_state_known:
                values[pipe] = LEAK_CHECK_VALUE  # scanned before UT: scan again for a leak
            links, unknown = [], []  # directions of known pipe / unknown neighbours
            for d, nxt in self.around[pipe]:
                known = detected.get(nxt, public.get(nxt))
                if known is None:
                    unknown.append(d)
                elif known:
                    links.append(d)
            if not unknown:
                if leak_state_known:
                    self.settled.add(pipe)  # nothing left to learn here: skip it from now on
                continue
            # The pipe may also leave the map, so off-map directions count as open ends
            open_ends = unknown + [d for d in Dir if not view.in_bounds(d.step(pipe))]
            missing = max(0, 2 - len(links))  # pipes never dead-end inside the map
            straight = links[0].opposite if len(links) == 1 else None
            for d in unknown:
                if missing == 0:
                    chance = BRANCH_CHANCE  # both links known: only a T-junction fits here
                elif d is straight:
                    chance = STRAIGHT_CHANCE
                elif straight in open_ends:
                    chance = max(BRANCH_CHANCE, (1 - STRAIGHT_CHANCE) / (len(open_ends) - 1))
                else:
                    chance = min(1.0, missing / len(open_ends))  # the missing links share the open ends
                nxt = d.step(pipe)
                values[nxt] = max(values.get(nxt, 0.0), chance * (1 + bonus))
        return values

    # --- Pathfinding -----------------------------------------------------------------------
    @staticmethod
    def _travel(view: BotView, dist: int, first: Dir | None) -> int:
        """Moves needed to walk dist steps starting with `first`, ball and chain included.

        A chained bot needs two moves per step (drag, then step) until the chain wears off; a
        drag already started towards `first` makes that first step cost one move only.
        A MOVE may therefore leave ``view.me`` unchanged, so plans are rebuilt every move.
        """
        chained = view.chained_moves()
        if not chained or not dist:
            return dist
        moves = 0
        if first is not None and first is view.dragging:
            moves, dist, chained = 1, dist - 1, chained - 1  # the drag is done: this move steps
        slow = min(dist, (chained + 1) // 2)  # steps started while still chained cost two moves
        return moves + 2 * slow + (dist - slow)

    def _cheapest_path(
        self, view: BotView, moves_after: Callable[[Pos], int | None], budget: int
    ) -> tuple[int, Dir] | None:
        """(travel moves, first step) to the cheapest goal square, or None if none fits.

        ``moves_after(pos)`` is None for non-goal squares, else the moves still needed there;
        travel + those moves must fit into ``budget``. The bot's own square never counts.
        """
        best: tuple[int, Dir] | None = None
        for pos, dist, first in self._reachable(view):
            if not dist:
                continue
            if best is not None and self._travel(view, dist, view.dragging) > best[0]:
                break  # every farther square costs more moves
            needed = moves_after(pos)
            if needed is None:
                continue
            cost = self._travel(view, dist, first)
            if cost + needed <= budget and (best is None or cost < best[0]):
                best = (cost, first)
        return best

    def _reachable(self, view: BotView) -> Iterator[tuple[Pos, int, Dir | None]]:
        """Breadth-first search over walkable squares: yield (square, distance, first step).

        While dragging, the first step in the dragged direction is searched first, so ties keep
        the bot moving the same way (flipping directions every move, it would never step).
        """
        start = view.me
        yield start, 0, None
        seen = {start}
        queue = deque([(start, 0, None)])
        while queue:
            pos, dist, first = queue.popleft()
            around = self.around[pos]
            if pos == start and view.dragging is not None:
                around = sorted(around, key=lambda step: step[0] is not view.dragging)
            for d, nxt in around:
                if nxt not in seen and view.is_walkable(nxt):
                    seen.add(nxt)
                    step = first or d
                    yield nxt, dist + 1, step
                    queue.append((nxt, dist + 1, step))
