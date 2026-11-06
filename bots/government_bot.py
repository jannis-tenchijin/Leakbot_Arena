"""GovernmentBot: an example competitor that works like a careful public water utility.

Strategy:
    Phase 1  Find a pipe, then follow the pipe network with steps and detects. Every newly
             discovered pipe square pays +1, until the bot can afford the ultrasonic test.
    Buy      As soon as ``view.score >= view.cost(PowerUp.UT)``: walk to the waterworks (水道局)
             and buy UT there (15 points; on maps without a waterworks it buys on the spot).
    Phase 2  Keep scanning along the pipes. With UT every scan also reports leaks. New leaks
             appear during the round (``view.leaks_total`` grows), so once every known leak is
             accounted for, pipes scanned before the latest new leak are scanned again.
    Repair   A reported leak becomes a job, started only if all of it fits into the round:
             dig it up (+5), then for every square its pipe connects to (``connected_neighbors``)
             unearth it, install a valve and close it; fix the leak (+40); reopen each valve
             (+5 each) and refill every hole the bot dug. Broken pipes other bots left open are
             jobs too (fixing them still pays +40), and so are their fixed leaks whose valves
             are still closed (+5 per reopened valve).
    Ending   If the fix no longer fits into the round, the bot reopens the valves it closed
             (-10 each at the end) and refills its holes (-2 each) instead; when time is short,
             valves come first.
    Punk     From move 200 a punk pickup lies on the map: if it is close, the bot detours to
             collect it (+10, denied to the opponents). As a punk it then finishes its job and
             chases the leading unchained opponent (highest score) to put its one ball and chain
             on it; it gives up after 60 moves of chasing or near the end of the round, and then
             chains any adjacent opponent (late in the round, own open holes come first).
             When chained itself, every step costs two moves: it keeps dragging the same way and
             plans its jobs with the doubled cost.

How it chooses where to scan: every square worth scanning gets a value (how likely it holds
pipe, plus a leak bonus while a leak is still unaccounted for). A breadth-first search over
walkable squares then picks the step or detect with the best value per move. Buried pipes are
invisible, so their course is guessed from earlier detects: pipes run in long straight lines,
never dead-end inside the map, and form one connected network (so new pipe is only searched
next to known pipe).

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

# With more moves left than this, any job fits (travel <= ~100 moves even chained, a repair
# <= ~50), so the bot skips the time estimate. Late in the round own holes also come before
# chaining an opponent.
LATE = 150
UT_MIN_MOVES = 50  # walk to the waterworks for UT only if this many moves remain afterwards
BLOCKED_LIMIT = 20  # give up a job after this many moves without any reachable step of it

# Punk pickup: +10 is about what 10-15 moves of scanning earn (well under one point per move,
# since most squares around the bot are already scanned by move 200). Taking it also denies it
# to the opponents (and their ball and chain), so a detour of up to 15 moves is worth it.
PUNK_RANGE = 15
# Punk chase: walk after the leading opponent to chain it (two moves per step for 100 of its
# moves is worth a long walk), but give up after CHASE_LIMIT moves or near the end of the round
CHASE_LIMIT = 60
CHASE_MIN_MOVES = 20

UNEARTH, FIX, REFILL = ActionType.UNEARTH, ActionType.FIX, ActionType.REFILL
INSTALL, CLOSE, OPEN = ActionType.INSTALL_VALVE, ActionType.CLOSE_VALVE, ActionType.OPEN_VALVE
WORK = (UNEARTH, INSTALL, CLOSE, OPEN, FIX, REFILL)  # actions on a neighbouring square

Tasks = dict[Pos, ActionType]  # square -> the next action it needs


def distance(a: Pos, b: Pos) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


class GovernmentBot(BaseBot):
    name = "GovernmentBot"

    def on_game_start(self, view: BotView) -> None:
        # In-grid neighbours of every square, computed once (Dir.step is slow in hot loops)
        self.around: dict[Pos, list[tuple[Dir, Pos]]] = {
            (x, y): [(d, d.step((x, y))) for d in Dir if view.in_bounds(d.step((x, y)))]
            for y in range(view.height)
            for x in range(view.width)
        }
        # Private memory for this round (the view does not say who dug or closed what)
        self.pipes: set[Pos] = {pos for pos, has_pipe in view.detected.items() if has_pipe}
        self.settled: set[Pos] = set()  # known pipes whose neighbours are all known
        self.leaks: set[Pos] = set()  # squares UT reported as leaking that are still buried
        self.known_leaks: set[Pos] = set()  # every leak this bot knows of (reported, dug up or fixed)
        self.checked: dict[Pos, int] = {}  # pipe square -> move of its latest UT reading
        self.leak_count = view.leaks_total  # leaks on the map when last looked
        self.leak_epoch = 0  # move the latest new leak appeared: older UT readings are stale
        self.my_holes: set[Pos] = set()  # holes this bot dug that are still open
        self.closed: set[Pos] = set()  # valves this bot closed that are still closed (maybe buried)
        self.public: dict[Pos, bool] = {}  # squares anyone dug up: pos -> has pipe (everyone sees it)
        self.broken: set[Pos] = set()  # open holes with a leaking pipe: anyone may repair them
        self.reopen: set[Pos] = set()  # fixed leaks next to a closed valve: reopening it pays +5
        self.job: Pos | None = None  # the leak this bot is repairing
        self.blocked = 0  # moves in a row the job had no reachable step
        self.chase_moves = 0  # moves spent walking after the opponent to chain

    def act(self, view: BotView) -> Action:
        self._remember(view)

        # 1. As a punk: chain an adjacent opponent (the leader while chasing)
        action = self._chain(view)
        if action is not None:
            return action

        # 2. The repair in progress, or the clean-up of valves and holes left over
        action = self._continue_job(view)
        if action is not None:
            return action

        # 3. As a punk with nothing left open: chase the leading opponent to chain it
        action = self._chase(view)
        if action is not None:
            return action

        # 4. Phase switch: buy UT when affordable (standing on the waterworks, if the map has one)
        ut = self._ut_trip(view)
        if ut is not None and ut[0] == 0:
            return ut[1]

        # 5. The nearest of a new job, the punk pickup and the waterworks, else 6. scan
        options = [opt for opt in (self._start_job(view), self._path_to_punk(view), ut) if opt is not None]
        if options:
            return min(options, key=lambda opt: opt[0])[1]  # ties: the job first
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
                self.checked[pos] = view.move - 1  # the scan happened on the previous move
                if leaking:
                    self.leaks.add(pos)
                    self.known_leaks.add(pos)
            action = result.action
            if result.success and action.type in WORK:  # the bot did not move: the target is next to it
                target = action.direction.step(view.me)
                if action.type is UNEARTH:
                    info = view.cell(target)
                    self.my_holes.add(target)
                    self.public[target] = bool(info.has_pipe)
                    if info.has_pipe:
                        self.pipes.add(target)  # a valve square may not have been scanned before
                    if info.pipe_state is PipeState.BROKEN:
                        self.known_leaks.add(target)
                elif action.type is CLOSE:
                    self.closed.add(target)
                elif action.type is OPEN:
                    self.closed.discard(target)

        if view.leaks_total > self.leak_count:  # a new leak appeared somewhere this move
            self.leak_count, self.leak_epoch = view.leaks_total, view.move

        # Forget holes that were filled again and valves someone reopened; a closed valve under
        # a refill stays closed (nobody can see or turn it there)
        self.my_holes = {pos for pos in self.my_holes if view.surface(pos) is Surface.UNEARTHED}
        self.closed = {
            pos
            for pos in self.closed
            if view.surface(pos) is Surface.UNTOUCHED or view.cell(pos).valve is False
        }
        self.leaks = {pos for pos in self.leaks if view.surface(pos) is Surface.UNTOUCHED}

        # Other bots dig too: read the public map (in single player only this bot digs)
        if view.n_bots > 1:
            self._read_public_map(view)
        else:
            self.broken = {pos for pos in self.my_holes if view.cell(pos).pipe_state is PipeState.BROKEN}

    def _read_public_map(self, view: BotView) -> None:
        """Note every open hole: its pipe is public, and broken or valved ones may be jobs."""
        self.broken, self.reopen = set(), set()
        for pos in self.around:
            if view.surface(pos) is not Surface.UNEARTHED:
                continue
            info = view.cell(pos)
            self.public[pos] = bool(info.has_pipe)
            if info.has_pipe:
                self.pipes.add(pos)
            if info.pipe_state is PipeState.BROKEN:
                self.broken.add(pos)
            if info.pipe_state in (PipeState.BROKEN, PipeState.FIXED):
                self.known_leaks.add(pos)
            if info.valve is False:  # a closed valve next to a fixed leak: reopening it may pay +5
                for _, leak in self.around[pos]:
                    fixed = view.cell(leak).pipe_state is PipeState.FIXED
                    if fixed and pos in view.connected_neighbors(leak):
                        self.reopen.add(leak)

    # --- Repair jobs -----------------------------------------------------------------------
    def _job_tasks(self, view: BotView, leak: Pos) -> Tasks | None:
        """The next action each square of a leak's repair needs ({} = done, None = impossible)."""
        info = view.cell(leak)
        if info.revealed and not info.pipe_dirs:
            return None  # the pipe's course is hidden: no way to know where the valves go
        if info.pipe_state is PipeState.FIXED:
            return self._restore_tasks(view, leak)
        if info.surface is Surface.UNTOUCHED:
            return {leak: UNEARTH}  # find it (+5); its pipe directions show once it is dug up
        tasks = {}
        for pos in view.connected_neighbors(leak):  # every square the leaking pipe feeds needs a closed valve
            kind = self._isolate_step(view, pos)
            if kind is UNEARTH and view.cell(pos).building:
                return None  # the waterworks cannot be dug up (the engine never puts a leak there)
            if kind is not None:
                tasks[pos] = kind
        return tasks or {leak: FIX}

    def _isolate_step(self, view: BotView, pos: Pos) -> ActionType | None:
        """Unearth, install, close: the next step to valve off one neighbour (None = closed)."""
        info = view.cell(pos)
        if info.surface is Surface.UNTOUCHED:
            return None if pos in self.closed else UNEARTH  # own closed valve under a refill counts
        if info.valve is None:
            return INSTALL
        return CLOSE if info.valve else None

    def _restore_tasks(self, view: BotView, leak: Pos) -> Tasks:
        """After the fix: reopen every valve around the leak (+5 each), refill own holes."""
        tasks = {leak: REFILL} if leak in self.my_holes else {}
        for pos in view.connected_neighbors(leak):
            info = view.cell(pos)
            if info.surface is Surface.UNTOUCHED:
                if pos in self.closed:
                    tasks[pos] = UNEARTH  # own valve buried closed: dig it up to reopen it
            elif info.valve is False:
                tasks[pos] = OPEN
            elif pos in self.my_holes and info.pipe_state is not PipeState.BROKEN:
                tasks[pos] = REFILL  # a leaking neighbour stays open: it is a job of its own
        return tasks

    def _bury_tasks(self, view: BotView, leak: Pos) -> Tasks:
        """Every square next to the leak is a hole: refill a closed own valve to stand on it."""
        return {
            pos: REFILL
            for pos in view.connected_neighbors(leak)
            if pos in self.my_holes and view.cell(pos).valve is False
        }

    def _leftovers(self, view: BotView) -> Tasks:
        """Own closed valves (-10 each at the end) and own refillable holes (-2 each)."""
        tasks = {pos: OPEN if view.surface(pos) is Surface.UNEARTHED else UNEARTH for pos in self.closed}
        for pos in self.my_holes - self.closed:
            if view.cell(pos).pipe_state is not PipeState.BROKEN:
                tasks[pos] = REFILL
        return tasks

    def _continue_job(self, view: BotView) -> Action | None:
        """Next step of the current job, or of the clean-up when there is no job (None: nothing)."""
        if self.job is not None:
            leak = self.job
            tasks = self._job_tasks(view, leak)
            if not tasks or self.blocked > BLOCKED_LIMIT:
                self.job = None  # done, impossible or stuck
            elif view.cell(leak).pipe_state is PipeState.FIXED:
                return self._blocked(self._do(view, *self._tiers(view, tasks)))  # reopen and refill
            elif self._fits(view, leak, view.me, full=False):
                action = self._do(view, tasks)
                if action is None and FIX in tasks.values():
                    action = self._do(view, self._bury_tasks(view, leak))
                return self._blocked(action)  # None: wait for a blocked square, do something else
            else:
                self.job = None  # the round ends before the fix: undo the valves and holes instead
        self.blocked = 0
        return self._do(view, *self._tiers(view, self._leftovers(view)))

    def _blocked(self, action: Action | None) -> Action | None:
        self.blocked = 0 if action is not None else self.blocked + 1
        return action

    def _tiers(self, view: BotView, tasks: Tasks) -> list[Tasks]:
        """Restore or clean-up tasks in one go, or valves first (-10 each) if time is short."""
        valves = {pos: kind for pos, kind in tasks.items() if kind is not REFILL}
        counts = [(pos, self._work_count(pos, kind)) for pos, kind in tasks.items()]
        if not valves or self._route_cost(view, view.me, [counts]) <= view.moves_left:
            return [tasks]
        return [valves, {pos: kind for pos, kind in tasks.items() if kind is REFILL}]

    def _work_count(self, pos: Pos, kind: ActionType) -> int:
        """Actions a restore/clean-up square still needs: reopen (dig up first if buried), refill."""
        if kind is UNEARTH:
            return 3  # unearth, open, refill
        return 1 + (kind is OPEN and pos in self.my_holes)

    def _start_job(self, view: BotView) -> tuple[int, Action] | None:
        """(travel moves, action) toward the nearest job that fits into the round; starts it when adjacent."""
        if self.job is not None:
            return None
        tasks_of = {}
        for leak in self.leaks | self.broken | self.reopen:
            tasks = self._job_tasks(view, leak)
            if tasks:
                tasks_of[leak] = tasks
        leak_at = {  # task square -> its leak
            pos: leak
            for leak, tasks in tasks_of.items()
            for pos, kind in tasks.items()
            if self._ready(view, pos, kind)
        }
        if not leak_at:
            return None

        for d, pos in self.around[view.me]:
            leak = leak_at.get(pos)
            if leak is not None and self._fits(view, leak, view.me):
                self.job, self.blocked = leak, 0
                return 0, Action(tasks_of[leak][pos], d)

        late = view.moves_left <= LATE

        def moves_after_arrival(spot: Pos) -> int | None:
            leaks = {leak_at[pos] for _, pos in self.around[spot] if pos in leak_at}
            costs = [self._job_cost(view, leak, spot) if late else 1 for leak in leaks]
            return min(costs, default=None)

        path = self._cheapest_path(view, moves_after_arrival, view.moves_left)
        return (path[0], Action.move(path[1])) if path else None

    def _ready(self, view: BotView, pos: Pos, kind: ActionType) -> bool:
        """Can the action be done on pos once the bot stands next to it?"""
        if kind is not UNEARTH:
            return True
        info = view.cell(pos)
        return info.bot in (None, view.bot_id) and info.item is None and not info.building

    def _do(self, view: BotView, *tiers: Tasks) -> Action | None:
        """Act on an adjacent task square, else walk next to the nearest one; earlier tiers first."""
        for tasks in tiers:
            ready = {pos: kind for pos, kind in tasks.items() if self._ready(view, pos, kind)}
            for d, pos in self.around[view.me]:
                kind = ready.get(pos)
                if kind is not None and self._need(pos, kind) <= view.moves_left:
                    return Action(kind, d)
            spots: dict[Pos, int] = {}  # square to stand on -> moves of work needed there
            for pos, kind in ready.items():
                need = self._need(pos, kind)
                for _, spot in self.around[pos]:
                    spots[spot] = min(spots.get(spot, need), need)
            for pos, kind in tasks.items():
                if kind is UNEARTH and view.cell(pos).item is not None:
                    spots[pos] = 0  # the punk pickup lies on it: collect it first by stepping onto it
            path = self._cheapest_path(view, spots.get, view.moves_left) if spots else None
            if path is not None:
                return Action.move(path[1])
        return None

    def _need(self, pos: Pos, kind: ActionType) -> int:
        """Moves an action needs to be worth starting: reopening a buried valve takes 3."""
        return 3 if kind is UNEARTH and pos in self.closed else 1

    # --- Time estimates ----------------------------------------------------------------------
    def _fits(self, view: BotView, leak: Pos, start: Pos, full: bool = True) -> bool:
        """Does the repair (through the fix only, unless full) fit into the moves left?"""
        return view.moves_left > LATE or self._job_cost(view, leak, start, full) <= view.moves_left

    def _job_cost(self, view: BotView, leak: Pos, start: Pos, full: bool = True) -> int:
        """Estimated moves to finish a leak's repair from start (stops after the fix unless full)."""
        info = view.cell(leak)
        if info.pipe_state is PipeState.FIXED:
            tasks = self._restore_tasks(view, leak)
            counts = [(pos, self._work_count(pos, kind)) for pos, kind in tasks.items()]
            return self._route_cost(view, start, [counts])
        links = view.connected_neighbors(leak) if info.revealed else self._guess_links(view, leak)
        dig = info.surface is Surface.UNTOUCHED
        # Without a free square next to the leak, a closed valve is refilled to stand on (dug up later)
        free = any(view.surface(pos) is Surface.UNTOUCHED for _, pos in self.around[leak] if pos not in links)
        phases = [
            [(leak, 1)] if dig else [],
            [(pos, n) for pos in links if (n := self._isolate_count(view, pos))],
            [(leak, 1 if free else 3)],
        ]
        if full:
            restore = [(leak, 1)] if dig or leak in self.my_holes else []
            phases.append(restore + [(pos, n) for pos in links if (n := self._reopen_count(view, pos))])
        return self._route_cost(view, start, phases)

    def _guess_links(self, view: BotView, leak: Pos) -> list[Pos]:
        """Neighbours a still buried leak may connect to (cautious): known pipe and unknown squares."""
        detected = view.detected
        return [pos for _, pos in self.around[leak] if detected.get(pos, self.public.get(pos)) is not False]

    def _isolate_count(self, view: BotView, pos: Pos) -> int:
        """Actions left to valve off a neighbour: unearth, install, close."""
        info = view.cell(pos)
        if info.surface is Surface.UNTOUCHED:
            return 0 if pos in self.closed else 3
        return 0 if info.valve is False else 1 + (info.valve is None)

    def _reopen_count(self, view: BotView, pos: Pos) -> int:
        """Actions after the fix on a neighbour that will hold a closed valve: (dig,) open, refill."""
        if view.surface(pos) is Surface.UNTOUCHED:
            return 3 if pos in self.closed else 2  # buried own valve / the bot digs it itself
        return 1 + (pos in self.my_holes)

    @staticmethod
    def _route_cost(view: BotView, start: Pos, phases: list[list[tuple[Pos, int]]]) -> int:
        """Estimated moves to do (square, actions) phase by phase, nearest square first.

        From start the bot walks next to the first square; between two squares it needs about
        their distance + 1 steps (it stands beside them, walking around holes). Steps cost two
        moves while chained.
        """
        pos, steps, actions = None, 0, 0
        for phase in phases:
            todo = dict(phase)
            while todo:
                ref = start if pos is None else pos
                target = min(todo, key=lambda t: (distance(ref, t), t))
                dist = distance(ref, target)
                steps += abs(dist - 1) if pos is None else dist + (dist > 0)
                actions += todo.pop(target)
                pos = target
        return actions + steps + min(steps, view.chained_moves())

    # --- Power-ups ---------------------------------------------------------------------------
    def _ut_trip(self, view: BotView) -> tuple[int, Action] | None:
        """(travel moves, action) to buy UT: 0 = buy now, else the step toward the waterworks."""
        if view.has(PowerUp.UT) or view.score < view.cost(PowerUp.UT):
            return None
        target = view.waterworks_pos
        if target is None or target == view.me:
            return 0, Action.buy(PowerUp.UT)
        # UT pays off through leak repairs: only walk there if enough of the round remains
        budget = view.moves_left - UT_MIN_MOVES
        path = self._cheapest_path(view, lambda pos: 0 if pos == target else None, budget)
        return (path[0], Action.move(path[1])) if path else None

    def _path_to_punk(self, view: BotView) -> tuple[int, Action] | None:
        """(travel moves, first step) to the punk pickup if it lies within PUNK_RANGE moves."""
        target = view.punk_pos
        if target is None:
            return None
        path = self._cheapest_path(view, lambda pos: 0 if pos == target else None, PUNK_RANGE)
        return (path[0], Action.move(path[1])) if path else None

    def _chase_target(self, view: BotView) -> Pos | None:
        """Where the opponent to chain stands: the highest score among unchained ones (lowest id on ties)."""
        rivals = [
            (view.scores[bot_id], -bot_id, pos)
            for bot_id, pos in view.bot_positions().items()
            if bot_id != view.bot_id and view.chained_moves(bot_id) == 0
        ]
        return max(rivals)[2] if rivals else None

    def _chasing(self, view: BotView) -> bool:
        """A punk with its ball and chain still goes after the leader (until it gives up)."""
        return view.can_chain and self.chase_moves < CHASE_LIMIT and view.moves_left >= CHASE_MIN_MOVES

    def _chase(self, view: BotView) -> Action | None:
        """Step toward a square next to the opponent to chain; only with no job, hole or closed valve open."""
        if not self._chasing(view) or self.job is not None or self.my_holes or self.closed:
            return None
        target = self._chase_target(view)
        if target is None:
            return None
        path = self._cheapest_path(
            view, lambda pos: 0 if distance(pos, target) == 1 else None, view.moves_left - CHASE_MIN_MOVES
        )
        if path is None:
            return None  # walled in or too far: work as usual meanwhile
        self.chase_moves += 1
        return Action.move(path[1])

    def _chain(self, view: BotView) -> Action | None:
        """Ball and chain on an adjacent opponent: the chase target while chasing, else the strongest."""
        if not view.can_chain or (self.my_holes and view.moves_left <= LATE):
            return None  # late in the round an own open hole is finished first
        at = {pos: bot_id for bot_id, pos in view.bot_positions().items() if bot_id != view.bot_id}
        if self._chasing(view):  # battle royale: save the chain for the leader
            target = self._chase_target(view)
            at = {target: at[target]} if target in at else {}
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
        # Leak hunting pays only while some leak on the map is still unaccounted for
        hunting = view.has(PowerUp.UT) and view.leaks_total > len(self.known_leaks)
        bonus = LEAK_CHECK_VALUE if hunting else 0.0
        detected, public = view.detected, self.public
        if not self.pipes:  # no pipe found yet: any unscanned square might hold one
            unscanned = (pos for pos in self.around if pos not in detected and pos not in public)
            return {pos: BASE_CHANCE * (1 + bonus) for pos in unscanned}

        values: dict[Pos, float] = {}
        if hunting:  # rescan buried pipes whose last UT reading is older than the latest new leak
            for pipe in self.pipes:
                if (
                    self.checked.get(pipe, -1) < self.leak_epoch
                    and pipe not in public  # dug up once: a new leak never appears there
                    and pipe not in self.known_leaks
                    and pipe != view.waterworks_pos
                ):
                    values[pipe] = LEAK_CHECK_VALUE
        for pipe in self.pipes - self.settled:
            links, unknown = [], []  # directions of known pipe / unknown neighbours
            for d, nxt in self.around[pipe]:
                known = detected.get(nxt, public.get(nxt))
                if known is None:
                    unknown.append(d)
                elif known:
                    links.append(d)
            if not unknown:
                self.settled.add(pipe)  # nothing left to learn around it: skip it from now on
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
