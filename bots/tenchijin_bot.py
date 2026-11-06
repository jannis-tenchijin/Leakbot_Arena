"""TenchijinBot: earn 25 points, buy the satellite at the waterworks, then dig up the riskiest pipes.

Strategy:
1. Explore: follow pipes and scan with detect (+1 per new pipe square). Once it can afford the
   satellite, walk to the waterworks (水道局, power-ups are only sold there) and buy it.
2. Hunt: the satellite grades every pipe square A..E. Dig the untouched squares graded B or
   riskier, best "expected points per move" first (so a near D can beat a far E). An intact
   square is refilled at once; a leak gets the valve repair below.
3. Recheck: the image is retaken every 50 moves and new leaks appear at moves 200, 300 and 400 on
   B+ squares (and look like one, mostly E, only on the next image). Every new image, new leak or
   finished job starts a new target search, and a leak that is not on the image yet raises the
   odds of every B+ square it may hide in. So after its last known leak the bot keeps rechecking
   the B+ squares nobody has dug, and goes straight for a new leak once the image shows it.

Valve repair of a leak L (5 found + 40 fixed + 5 per reopened valve): for every square L's pipe
connects to (view.connected_neighbors): unearth, install a valve, close it; then fix L, reopen
every valve and refill all the holes. The steps are done nearest first from where the bot stands,
re-derived from the board every move, so another bot's help or interference is simply absorbed.
A job is only started if its expected work fits in the moves left; when the end of the round is
near ("rushed"), the bot drops unfinished work and spends its last moves on the steps that pay:
fix, reopen its closed valves (-10 each at the end), refill its holes (-2 each).

Punk upgrade (pickup from move 200): the pickup is one more target in the same "expected points
per move" scoring, so the bot detours for it when it is close. As a punk it finishes (or drops,
if not started) its current job, then chases the opponent with the most points that is not
chained yet (re-targeting if the lead changes) and chains it as soon as it stands next to it. The
chase ends after CHASE_MOVES moves or near the end of the round; then it works on as usual. While
chained itself a step costs two moves: the bot keeps its path until it has really moved, and
prices travel at two moves per step.

Run it:    python main.py --bots tenchijin_bot:TenchijinBot
Benchmark: python simulate.py --bots tenchijin_bot --rounds 100 --seed 1
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterator, Mapping

from leakbots import (
    DEFAULT_CONFIG, Action, ActionType, BaseBot, BotView, Dir, PipeState, Pos, PowerUp, Surface,
)
from leakbots.model import RISK_GRADES

SATELLITE = PowerUp.SATELLITE
DIG_GRADES = ("B", "C", "D", "E")  # B or riskier: where leaks hide (new ones too, once a satellite is up)
MIN_MOVES_TO_BUY = 40  # with fewer moves left the satellite rarely pays back its price
IDLE_REPLAN_EVERY = 5  # moves between target searches while there is nothing to do
# The punk pickup pays points_punk. With opponents, taking it also denies them those points and
# the ball and chain they could put on us: counted as this share of the pickup on top (a guess).
PUNK_DENY_SHARE = 0.5
# As a punk it chases the leading opponent to chain it, but gives up after CHASE_MOVES moves
# (a bot running around is hard to catch) and does not start with few moves left (worth little)
CHASE_MOVES = 40
CHASE_MIN_MOVES_LEFT = 50

# Valve repair bookkeeping
REPAIR_WALK_PER_VALVE = 3  # moves walking between the squares of a repair, per valve
END_MARGIN = 2  # spare moves kept when checking that started work still fits in the round
PATIENCE = 10  # moves a job may stay blocked (e.g. a bot stands on its square) before we drop it
SKIP_MOVES = 30  # a dropped target is left alone this long
# Steps of a job, most urgent first when several can be done from where the bot stands
TASK_ORDER = ("fix", "open", "close", "install", "unearth", "refill", "step")
DIG_TASKS = ("unearth", "step")  # tasks on untouched squares: better not stand on those
VALVE_TASKS = ("step", "unearth", "install", "close")  # a valve still to close: revisit to reopen it
ACTIONS = {
    "fix": Action.fix,
    "open": Action.open_valve,
    "close": Action.close_valve,
    "install": Action.install_valve,
    "unearth": Action.unearth,
    "refill": Action.refill,
    "step": Action.move,  # walk onto an object (the punk pickup) lying on a square to dig
}
# Points a step is worth when the round is about to end (only these are done then)
END_VALUE = {"fix": DEFAULT_CONFIG.points_leak_fixed, "open": DEFAULT_CONFIG.penalty_closed_valve,
             "refill": DEFAULT_CONFIG.penalty_open_pipe_hole}

# Phase 1: estimated chance that an undetected square holds pipe (pipes run straight for long)
P_BASE = 0.25  # nothing detected next to it (about 28 % of all squares hold pipe)
P_STRAIGHT = 0.8  # continues a straight line of two detected pipe squares
P_TURN = 0.5  # beside a pipe end that cannot run straight on
P_SIDE = 0.15  # beside a pipe that probably runs straight on
P_JUNCTION = 0.1  # beside a pipe whose two links are already known
P_MAX = 0.9
STEP_IF = 0.6  # step onto an undetected square this likely to be pipe (cheaper than detect + step)
MAX_SQUARE_VALUE = P_MAX * 5  # most a square can pay: itself plus a detect of its 4 neighbours


def leak_chances(n_pipes: int, n_leaks: int, decay: float) -> dict[str, float]:
    """P(leak | risk grade), derived from the engine's grading (``leakbots/pipes.py``).

    Every pipe square draws a level k = 0..3 with P(k) ~ decay**k. Intact pipes get
    RISK_GRADES[k] (A..D), leaks get RISK_GRADES[-1 - k] (E..B). So E is always a leak.
    """
    levels = len(RISK_GRADES) - 1
    total = sum(decay**k for k in range(levels))
    weight = [decay**k / total for k in range(levels)]
    n_intact = max(n_pipes - n_leaks, 0)
    chances = {}
    for i, grade in enumerate(RISK_GRADES):
        intact = n_intact * weight[i] if i < levels else 0.0
        leaky = n_leaks * weight[levels - i] if i > 0 else 0.0
        chances[grade] = leaky / (intact + leaky) if intact + leaky else 0.0
    return chances


def repair_moves(valves: int, walk: int = 1) -> int:
    """Moves of a whole valve repair, counted from unearthing the leak.

    Per valve: unearth, install, close (3), reopen (1), refill (1), plus walking between the
    squares (REPAIR_WALK_PER_VALVE steps, `walk` moves each: 2 while chained). The leak itself:
    unearth, fix, refill (3).
    """
    return (5 + REPAIR_WALK_PER_VALVE * walk) * valves + 3


def leak_value(valves: int, points: Mapping[str, int]) -> int:
    """Points of a fully repaired leak: found + fixed + one reopen bonus per valve."""
    return points["leak_found"] + points["leak_fixed"] + valves * points["valve_reopened"]


def dig_payoff(
    chance: float, moves_left: int, points: Mapping[str, int], valves: int = 2, walk: int = 1
) -> float:
    """Expected points of unearthing a pipe square now, given the moves left to repair or refill it."""
    found, penalty = points["leak_found"], points["open_hole_penalty"]
    if moves_left >= repair_moves(valves, walk):  # a leak gets the full repair; intact: refill
        return chance * leak_value(valves, points)
    if moves_left >= 2:  # intact: unearth + refill; a leak is found but its hole stays open
        return chance * (found - penalty)
    if moves_left == 1:  # unearth only: the hole stays open either way
        return chance * found - penalty
    return 0.0


def dig_work(chance: float, moves_left: int, valves: int = 2, walk: int = 1) -> float:
    """Expected moves of a dig: unearth + refill if intact, the whole repair if it leaks (and fits)."""
    repair = repair_moves(valves, walk)
    return (1 - chance) * 2 + chance * (repair if moves_left >= repair else 1)


def bfs(
    view: BotView, start: Pos, parent: dict[Pos, Pos | None], first: Dir | None = None
) -> Iterator[tuple[Pos, int]]:
    """Yield (square, distance) over walkable squares, nearest first. Fills parent for paths.

    The neighbours of start are queued `first` direction first, so every shortest-path tie is
    broken in its favour (keeps a half-dragged step when chained).
    """
    parent[start] = None
    queue = deque([(start, 0)])
    while queue:
        pos, dist = queue.popleft()
        yield pos, dist
        for d in sorted(Dir, key=lambda e: e is not first) if pos == start else Dir:
            nxt = d.step(pos)
            if nxt not in parent and view.is_walkable(nxt):
                parent[nxt] = pos
                queue.append((nxt, dist + 1))


def path_to(parent: Mapping[Pos, Pos | None], goal: Pos) -> list[Pos]:
    """Squares to walk from the BFS start to goal (start excluded, goal included)."""
    path = []
    while parent[goal] is not None:
        path.append(goal)
        goal = parent[goal]
    return path[::-1]


def direction(a: Pos, b: Pos) -> Dir | None:
    """Direction from a to its neighbour b (None if b is not next to a)."""
    return next((d for d in Dir if d.step(a) == b), None)


def manhattan(a: Pos, b: Pos) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def repairable(view: BotView, pos: Pos) -> bool:
    """A leak at pos can be fixed: some square next to it is not one of the squares to valve off
    (those are dug open by then). Not so e.g. for a corner square whose pipe turns there."""
    nbrs = view.connected_neighbors(pos)
    return any(view.in_bounds(n := d.step(pos)) and n not in nbrs for d in Dir)


def travel_moves(view: BotView, steps: int) -> int:
    """Moves needed to walk `steps` squares: two per step while the ball and chain lasts.

    Chained for c more moves, the first ceil(c / 2) steps need a drag each (an odd last chained
    move drags, the chain wears off, then the step is free). Ignores a drag already made.
    """
    return steps + min(steps, (view.chained_moves() + 1) // 2)


class TenchijinBot(BaseBot):
    name = "TenchijinBot"

    def on_game_start(self, view: BotView) -> None:
        self.my_holes: set[Pos] = set()  # pipe squares this bot dug that are still open
        self.my_closed: set[Pos] = set()  # valves this bot closed and has not seen reopened
        self.job: Pos | None = None  # square to dig up, an open leak to repair, or the punk pickup
        self.job_chance = 0.0  # its chance to leak
        self.punk_goal = False  # the job is the punk pickup: walk onto it instead of digging
        self.path: list[Pos] = []  # squares still to walk (to a square next to the work, or onto a goal)
        self.next_plan = 0  # earliest move for the next target search while idle
        self.stalled = 0  # moves the current job could not progress
        self.skip: dict[Pos, int] = {}  # dropped targets -> last move they are left alone
        self.punk_seen: Pos | None = None  # pickup square at the last search (it appears / vanishes)
        self.leaks_seen = view.leaks_total  # leaks on the map at the last search
        self.image_leaks = 0  # leaks on the map when our satellite image was taken
        self.bought_at: int | None = None  # move at which the satellite was bought
        self.chase_until: int | None = None  # punk: last move of the chase (None: not started)
        self.news = False  # a new image, leak or pickup since the last search

    def act(self, view: BotView) -> Action:
        self._remember(view)

        # Punk: the ball and chain is a one-off chance whenever an opponent stands next to us
        victim = self._chain_target(view)
        if victim is not None:
            return Action.chain(victim)
        action = self._chase(view)  # punk: go after the leader, then back to work
        if action is not None:
            return action

        # The current job and our own open holes / closed valves come first (penalties otherwise)
        if self.news:
            self._recheck(view)
        action = self._work(view)
        if action is not None:
            return action

        # Phase 1 -> 2: buy the satellite at the waterworks as soon as it is affordable
        action = self._satellite_trip(view)
        if action is not None:
            return action

        # Next job: D/E squares, B/C squares, open leaks of other bots, the punk pickup
        if self.job is None and view.move >= self.next_plan:
            self._plan(view)
            action = self._work(view)
            if action is not None:
                return action
        if not view.has(SATELLITE):
            return self._explore(view)
        return self._standby(view)

    def _standby(self, view: BotView) -> Action:
        """Nothing worth digging (every leak is accounted for): wait where the next one is likely.

        New leaks only appear on B-or-riskier squares nobody has dug, so the bot parks on the
        walkable square nearest their middle and waits there with detects (always valid, no
        points after the satellite). When the next image shows a new leak, it is close by.
        """
        undug = [
            p for p, r in view.satellite.items()
            if r.risk in DIG_GRADES and not view.cell(p).revealed
        ]
        if undug:
            cx = sum(p[0] for p in undug) / len(undug)
            cy = sum(p[1] for p in undug) / len(undug)
        else:
            cx, cy = (view.width - 1) / 2, (view.height - 1) / 2
        parent: dict[Pos, Pos | None] = {}
        reach = dict(bfs(view, view.me, parent, view.dragging))
        spot = min(reach, key=lambda q: (abs(q[0] - cx) + abs(q[1] - cy), reach[q], q))
        if spot == view.me or abs(spot[0] - cx) + abs(spot[1] - cy) >= abs(view.me[0] - cx) + abs(view.me[1] - cy) - 1:
            return Action.detect()  # parked (or no clearly better spot): wait here
        return Action.move(direction(view.me, path_to(parent, spot)[0]))

    # --- Memory ---------------------------------------------------------------------------------
    def _remember(self, view: BotView) -> None:
        """Record what our last action dug / closed, and notice news that calls for a new search."""
        r = view.last_result
        if r is not None and r.success and r.action is not None:
            kind = r.action.type
            if kind is not ActionType.MOVE and r.action.direction is not None:
                target = r.action.direction.step(view.me)  # only MOVE changes our square
                if kind is ActionType.UNEARTH:
                    self.my_holes.add(target)
                elif kind is ActionType.CLOSE_VALVE:
                    self.my_closed.add(target)
                elif kind is ActionType.OPEN_VALVE:
                    self.my_closed.discard(target)
            if kind is not ActionType.DETECT:
                self.stalled = 0  # the job moved on
        self.my_holes = {p for p in self.my_holes if view.surface(p) is Surface.UNEARTHED}
        # A valve someone else reopened no longer costs us (a refilled one stays unknown: keep it)
        self.my_closed = {
            p for p in self.my_closed if view.surface(p) is Surface.UNTOUCHED or view.cell(p).valve is False
        }

        # A new satellite image, a new leak or the pickup appearing / vanishing: search again now.
        # A job that has not started may then give way to a better one (see _recheck).
        image = view.has(SATELLITE) and view.satellite_age == 0  # retaken at the start of this move
        if image:
            self.image_leaks = view.leaks_total
        if image or view.leaks_total != self.leaks_seen or view.punk_pos != self.punk_seen:
            self.leaks_seen, self.punk_seen, self.next_plan = view.leaks_total, view.punk_pos, 0
            self.news = True

    def _recheck(self, view: BotView) -> None:
        """After news: replan a job that has not started yet, so a nearer or newly visible leak can
        win. Keep the old job if the search finds nothing (e.g. its travel was priced a bit higher
        mid-drag, near the end of the round)."""
        self.news = False
        if self.job is None or self._started(view):
            return
        old = self.job, self.job_chance, self.punk_goal, list(self.path)
        self._plan(view)
        if self.job is None:
            self.job, self.job_chance, self.punk_goal, self.path = old

    def _started(self, view: BotView) -> bool:
        """The job left marks on the board (an open leak counts): finish it rather than replan."""
        if self.job is None or self.punk_goal:
            return False
        if view.surface(self.job) is Surface.UNEARTHED:
            return True
        return any(n in self.my_holes for n in view.connected_neighbors(self.job))

    def _give_up(self, view: BotView, until: int) -> None:
        """Drop the current job; its target is left alone until move `until`."""
        if self.job is not None:
            self.skip[self.job] = until
        self.job, self.punk_goal, self.path, self.stalled, self.next_plan = None, False, [], 0, 0

    def _chain_target(self, view: BotView) -> Dir | None:
        """As a punk: direction of the strongest adjacent opponent that is not chained yet.

        While the chase is on, only the prey (the leader) is chained: a weaker bot that happens
        to stand next to us must not use up the one ball and chain.
        """
        if not view.can_chain:
            return None
        victims = []  # (score, -id, direction): highest score first, ties to the lower id
        for d in Dir:
            pos = d.step(view.me)
            bot = view.cell(pos).bot if view.in_bounds(pos) else None
            if bot is not None and bot != view.bot_id and view.chained_moves(bot) == 0:
                victims.append((view.scores[bot], -bot, d))
        if not victims:
            return None
        best = max(victims)
        if self._chase_on(view) and -best[1] != self._prey(view):
            return None
        return best[2]

    # --- Punk: chase an opponent to chain it ----------------------------------------------------
    @staticmethod
    def _prey(view: BotView) -> int | None:
        """The opponent to chain: the highest score among those not chained yet (ties: lower id)."""
        free = [
            (score, -i) for i, score in enumerate(view.scores)
            if i != view.bot_id and view.chained_moves(i) == 0
        ]
        return -max(free)[1] if free else None

    def _chase_on(self, view: BotView) -> bool:
        """A punk with its chain unused hunts the prey for at most CHASE_MOVES, not near the end."""
        if not view.can_chain or view.moves_left < CHASE_MIN_MOVES_LEFT or self._prey(view) is None:
            return False
        return self.chase_until is None or view.move <= self.chase_until

    def _chase(self, view: BotView) -> Action | None:
        """Walk next to the prey (it moves: the path is redone once it no longer ends next to it).

        Only from a clean state: a started repair, our open holes and closed valves are finished
        first (they cost points if the round ends), an unstarted dig job is dropped.
        """
        if not self._chase_on(view) or self.my_holes or self.my_closed or self._started(view):
            return None
        if self.chase_until is None:
            self.chase_until = view.move + CHASE_MOVES  # the chase starts now
        prey_pos = view.bot_positions()[self._prey(view)]
        goals = {n for d in Dir if view.is_walkable(n := d.step(prey_pos))}
        if self.job is not None:
            self.job, self.punk_goal, self.path = None, False, []
        self.next_plan = 0  # replan as soon as the chase ends
        return self._walk(view, goals) if goals else None

    # --- Walking --------------------------------------------------------------------------------
    def _walk(self, view: BotView, goals: set[Pos]) -> Action | None:
        """MOVE toward the nearest goal square (walkable). Keeps its path while it stays valid:
        while chained the first MOVE merely drags, and repeating it in the same direction steps."""
        if view.me in self.path:  # drop the squares we have really walked
            del self.path[: self.path.index(view.me) + 1]
        nxt = self.path[0] if self.path else None
        if not (nxt and self.path[-1] in goals and direction(view.me, nxt) and view.is_walkable(nxt)):
            self.path = []
            parent: dict[Pos, Pos | None] = {}
            for pos, dist in bfs(view, view.me, parent, view.dragging):
                if dist > 0 and pos in goals:
                    self.path = path_to(parent, pos)
                    break
        return Action.move(direction(view.me, self.path[0])) if self.path else None

    # --- Satellite purchase ---------------------------------------------------------------------
    def _satellite_trip(self, view: BotView) -> Action | None:
        """Buy the satellite: right here if there is no waterworks, else walk there first."""
        if view.has(SATELLITE) or view.score < view.cost(SATELLITE):
            return None
        shop = view.waterworks_pos
        if shop is None or view.me == shop:
            if view.moves_left < MIN_MOVES_TO_BUY:
                return None
            self.bought_at, self.image_leaks = view.move, view.leaks_total
            self.job, self.punk_goal, self.path, self.next_plan = None, False, [], 0
            return Action.buy(SATELLITE)
        action = self._walk(view, {shop})
        if action is None or view.moves_left - travel_moves(view, len(self.path)) < MIN_MOVES_TO_BUY:
            return None  # blocked (someone stands on it) or too late: keep exploring
        return action

    # --- Jobs: dig, repair, clean up ------------------------------------------------------------
    def _todo(self, view: BotView) -> dict[Pos, tuple[str | None, int]]:
        """Squares with work left -> (the step possible now or None, the actions still needed there).

        The job's repair steps, re-derived from the board, plus cleaning up our own holes and
        valves elsewhere. Ends the job once its leak is fixed (or the square turned out intact):
        reopening and refilling are then cleanup.
        """
        todo: dict[Pos, tuple[str | None, int]] = {}
        job_squares: set[Pos] = set()
        if self.job is not None and not self.punk_goal:
            t, cell = self.job, view.cell(self.job)
            nbrs = view.connected_neighbors(t)
            if cell.surface is Surface.UNTOUCHED and not cell.revealed:  # not dug yet: unearth (+ refill)
                todo[t] = ("step" if cell.item else "unearth", 2)
                job_squares = {t}
            elif cell.surface is Surface.UNEARTHED and cell.pipe_state is PipeState.BROKEN:
                if not repairable(view, t):
                    self._give_up(view, view.max_moves)  # nowhere to stand for the fix: leave it
                    return self._todo(view)
                job_squares = {t, *nbrs}
                ready = True  # every connected square holds a closed valve: fix
                for n in nbrs:
                    c = view.cell(n)
                    later = 1 + (n in self.my_holes or c.surface is Surface.UNTOUCHED)  # reopen + refill
                    if c.surface is Surface.UNTOUCHED:
                        todo[n] = ("step" if c.item else "unearth", 2 + later)
                    elif c.valve is None:
                        todo[n] = ("install", 2 + later)
                    elif c.valve:
                        todo[n] = ("close", 1 + later)
                    else:
                        todo[n] = (None, later)
                    ready = ready and c.surface is Surface.UNEARTHED and c.valve is False
                todo[t] = ("fix" if ready else None, 1 + (t in self.my_holes))
            else:  # fixed, intact, or dug and refilled already: the job is done
                self.job, self.path, self.next_plan = None, [], 0
        for sq in sorted((self.my_holes | self.my_closed) - job_squares):
            c = view.cell(sq)
            if c.surface is Surface.UNTOUCHED:  # our closed valve, buried by another bot: dig it up
                todo[sq] = ("unearth", 3)
            elif c.valve is False and sq in self.my_closed:
                todo[sq] = ("open", 1 + (sq in self.my_holes))
            elif c.pipe_state is PipeState.BROKEN:  # a leak we dug, left for later (no refill possible)
                nbrs = view.connected_neighbors(sq)
                if all(view.cell(n).valve is False for n in nbrs):
                    todo[sq] = ("fix", 1)
            elif sq in self.my_holes:
                todo[sq] = ("refill", 1)
        return todo

    def _estimate(self, view: BotView, todo: Mapping[Pos, tuple[str | None, int]]) -> int:
        """Rough moves to finish all of `todo`: its actions plus a nearest-first walk past the squares
        (a walk between two squares ~ their distance), one revisit per valve still to reopen."""
        actions = sum(n for _, n in todo.values())
        revisits = sum(1 for sq, (kind, _) in todo.items() if kind in VALVE_TASKS and sq != self.job)
        left = sorted(todo)
        pos = min(left, key=lambda s: manhattan(view.me, s))
        steps = max(manhattan(view.me, pos) - 1, 0)  # the first leg ends next to the square
        left.remove(pos)
        while left:
            sq = min(left, key=lambda s: manhattan(pos, s))
            steps += manhattan(pos, sq)
            pos = sq
            left.remove(sq)
        return actions + travel_moves(view, steps + revisits)

    @staticmethod
    def _can(view: BotView, sq: Pos, kind: str) -> bool:
        """The step is possible now (no bot or object on a square to dig, nothing on a square to step on)."""
        if kind == "unearth":
            c = view.cell(sq)
            return c.bot in (None, view.bot_id) and c.item is None and not c.building  # we can step off
        if kind == "step":
            return view.is_walkable(sq)
        return True

    def _work(self, view: BotView) -> Action | None:
        """Next action of the current job and of cleaning up our holes and valves (None: nothing)."""
        if self.punk_goal:
            if view.punk_pos != self.job:  # someone else took it
                self._give_up(view, view.move)
                return None
            action = self._walk(view, {self.job})
            if action is None:
                self._stall(view)
            return action

        todo = self._todo(view)
        if not todo:
            return None
        # Started work that no longer fits in the round: drop the job, do only what pays at the end
        started = self.job is None or self._started(view)
        rushed = started and view.moves_left < self._estimate(view, todo) + END_MARGIN
        if rushed and self.job is not None:
            self._give_up(view, view.max_moves)
            todo = self._todo(view)
        tasks = {
            sq: kind for sq, (kind, _) in todo.items()
            if kind is not None and (not rushed or kind in END_VALUE) and self._can(view, sq, kind)
        }
        if not tasks:
            if self.job is not None:
                self._stall(view)
            return None

        # A step we can take from here: the most urgent one
        here = sorted((TASK_ORDER.index(kind), sq) for sq, kind in tasks.items() if direction(view.me, sq))
        if here:
            sq = here[0][1]
            kind = tasks[sq]
            if sq == self.job and kind == "unearth":
                valves = len(view.connected_neighbors(sq))
                walk = 2 if view.chained_moves() else 1
                if dig_payoff(self.job_chance, view.moves_left, view.points, valves, walk) <= 0:
                    self._give_up(view, view.max_moves)  # too late in the round to dig this one
                    return None
            return ACTIONS[kind](direction(view.me, sq))

        # Walk next to a task: the nearest one, or when rushed the most valuable per move
        avoid = {sq for sq, (kind, _) in todo.items() if kind in DIG_TASKS}  # do not block our own digs
        if rushed:
            goals = self._best_end_stand(view, tasks)
        else:
            stands = {n: sq for sq in tasks for d in Dir if view.is_walkable(n := d.step(sq))}
            goals = {n for n in stands if n not in avoid} or set(stands)
        action = self._walk(view, goals) if goals else None
        if action is None and self.job is not None:
            self._stall(view)
        return action

    def _best_end_stand(self, view: BotView, tasks: Mapping[Pos, str]) -> set[Pos]:
        """End of round: the square next to the task with the most points per move (BFS distance)."""
        parent: dict[Pos, Pos | None] = {}
        best, goal = 0.0, None
        for pos, dist in bfs(view, view.me, parent, view.dragging):
            for d in Dir:
                kind = tasks.get(d.step(pos))
                if kind is not None and dist > 0:
                    value = END_VALUE[kind] / (travel_moves(view, dist) + 1)
                    if value > best:
                        best, goal = value, pos
        return {goal} if goal is not None else set()

    def _stall(self, view: BotView) -> None:
        """The job is blocked this move; drop it after PATIENCE such moves."""
        self.stalled += 1
        if self.stalled > PATIENCE:
            self._give_up(view, view.move + SKIP_MOVES)

    # --- Phase 1: explore -------------------------------------------------------------------
    def _explore(self, view: BotView) -> Action:
        """Earn +1 per new pipe square: walk along likely pipe, detect where it pays most."""
        det = view.detected
        memo: dict[Pos, float] = {}

        def unknown(pos: Pos) -> bool:  # stepping on or scanning it can still pay +1
            return pos not in det and not view.cell(pos).revealed

        def chance(pos: Pos) -> float:
            if pos not in memo:
                memo[pos] = self._pipe_chance(view, det, pos)
            return memo[pos]

        def scan_gain(pos: Pos) -> float:  # expected new pipe squares a detect at pos finds
            return sum(chance(n) for d in Dir if view.in_bounds(n := d.step(pos)) and unknown(n))

        # 1. On a pipe run: step straight onto the next likely pipe square (auto-detect pays +1)
        steps = [(chance(n), d) for d in Dir if view.is_walkable(n := d.step(view.me)) and unknown(n)]
        best_step = max(steps, key=lambda s: s[0], default=None)
        if best_step is not None and best_step[0] >= STEP_IF:
            return Action.move(best_step[1])

        # 2. Detect here, or walk to the square with the best expected points per move.
        #    Ties go to squares nearer the map centre, so empty regions are left inwards.
        cx, cy = (view.width - 1) / 2, (view.height - 1) / 2

        def key(rate: float, pos: Pos) -> tuple[float, float]:
            return round(rate, 6), -abs(pos[0] - cx) - abs(pos[1] - cy)

        best, best_square = key(scan_gain(view.me), view.me), None
        parent: dict[Pos, Pos | None] = {}
        for pos, dist in bfs(view, view.me, parent, view.dragging):
            if dist == 0:
                continue
            if MAX_SQUARE_VALUE / (dist + 1) < best[0]:
                break  # nothing farther away can beat the best rate (travel >= dist moves)
            value = scan_gain(pos) + (chance(pos) if unknown(pos) else 0.0)
            # +1: the detect once we get there; travel counts two moves per step while chained
            candidate = key(value / (travel_moves(view, dist) + 1), pos)
            if candidate > best:
                best, best_square = candidate, pos
        if best_square is None:
            return Action.detect()
        return Action.move(direction(view.me, path_to(parent, best_square)[0]))

    @staticmethod
    def _pipe_chance(view: BotView, det: Mapping[Pos, bool], pos: Pos) -> float:
        """Estimated chance that the undetected square pos holds pipe, from detected neighbours."""
        best = None
        for d in Dir:
            known = d.step(pos)  # a neighbour of pos ...
            if det.get(known) is not True:
                continue  # ... that is a detected pipe square
            links = [e for e in Dir if det.get(e.step(known)) is True]
            if det.get(d.step(known)) is True:  # pos, known and the square beyond form a line
                c = P_STRAIGHT
            elif len(links) >= 2:
                c = P_JUNCTION
            elif len(links) == 1:  # pos is beside the line; did the pipe turn at `known`?
                ahead = links[0].opposite.step(known)
                c = P_TURN if det.get(ahead) is False else P_SIDE
            else:  # no link known yet: about 2 of its open neighbours hold pipe
                open_sides = sum(1 for e in Dir if view.in_bounds(n := e.step(known)) and n not in det)
                c = min(P_MAX, 2 / max(open_sides, 1))
            best = c if best is None else max(best, c)
        return P_BASE if best is None else best

    # --- Phase 2/3: choose the next job ---------------------------------------------------------
    def _targets(self, view: BotView) -> dict[Pos, float]:
        """Squares worth working on -> chance to leak (1.0 for a leak already dug open)."""
        targets = {}
        for y in range(view.height):
            for x in range(view.width):
                pos = (x, y)
                if view.surface(pos) is Surface.UNEARTHED and view.cell(pos).pipe_state is PipeState.BROKEN:
                    targets[pos] = 1.0  # an open leak (another bot's, or ours left for later)
        if view.has(SATELLITE):
            targets.update(self._dig_chances(view))
        return {p: c for p, c in targets.items() if self.skip.get(p, -1) < view.move}

    def _dig_chances(self, view: BotView) -> dict[Pos, float]:
        """Untouched B+ squares nobody has dug yet -> chance to leak, from the satellite image.

        Leaks that appeared after the image was taken (view.leaks_total grew) hide on one of the
        undug B, C or D squares, each equally likely (the engine picks uniformly; the image shows
        their new grade only after the next refresh). Squares at the waterworks never leak.
        """
        sat = view.satellite
        # The view does not tell how many leaks a round started with: use the default, or more
        n_leaks = max(DEFAULT_CONFIG.n_leaks, self.image_leaks)
        prior = leak_chances(len(sat), n_leaks, DEFAULT_CONFIG.risk_decay)
        shop = view.waterworks_pos
        blocked = {shop, *view.connected_neighbors(shop)} if shop is not None else set()
        fresh = {
            p: r.risk for p, r in sat.items()
            if r.risk in DIG_GRADES and p not in blocked and not view.cell(p).revealed
        }
        top = RISK_GRADES[-1]
        pool = [p for p, grade in fresh.items() if grade != top]  # E squares already leak
        # Leaks still hiding = all leaks so far - leaks already dug up (public) - undug E squares.
        # Without this the bot kept digging B/C/D squares after every leak was found, because
        # their grade alone still gave them a small chance (the "inspection loop").
        known = sum(
            1 for y in range(view.height) for x in range(view.width)
            if view.cell((x, y)).revealed and view.cell((x, y)).pipe_state in (PipeState.BROKEN, PipeState.FIXED)
        ) + sum(1 for grade in fresh.values() if grade == top)
        hiding = max(0, view.leaks_total - known)
        chances = {p: 1.0 for p, grade in fresh.items() if grade == top}
        if hiding and pool:
            # Grade-based odds, plus an even share for leaks that appeared after the image was
            # taken (they are placed uniformly), scaled so they add up to the leaks still hiding.
            unseen = max(0, view.leaks_total - self.image_leaks)
            weight = {p: prior[fresh[p]] + unseen / len(pool) for p in pool}
            total = sum(weight.values())
            if total > 0:
                chances.update({p: min(1.0, w * hiding / total) for p, w in weight.items()})
        return chances

    def _plan(self, view: BotView) -> None:
        """Pick the job with the most expected points per move (travel + work).

        Travel is priced with travel_moves (two moves per step while chained), and a dig only
        counts the repair if it fits in the moves left, so the bot never starts work it cannot
        finish before the round ends.
        """
        self.job, self.path, self.punk_goal, self.stalled = None, [], False, 0
        # Nothing found: look again soon (every move before the satellite, for other bots' leaks)
        self.next_plan = view.move + (IDLE_REPLAN_EVERY if view.has(SATELLITE) else 1)
        targets = self._targets(view)
        punk = view.punk_pos
        if not targets and punk is None:
            return
        parent: dict[Pos, Pos | None] = {}
        dist = dict(bfs(view, view.me, parent, view.dragging))  # ties keep a half-dragged step
        points, best_rate = view.points, 0.0
        walk = 2 if view.chained_moves() else 1  # repair walking while chained

        # Punk pickup: points_punk (more with opponents, who would otherwise get it) for walking
        # onto it, no work after. Scored like any target, so it is a detour only when it is close:
        # e.g. it beats an E leak 10 steps away (55 / 29 = 1.9 points per move) within ~4 steps.
        if punk is not None and punk in dist and dist[punk] > 0 and self.skip.get(punk, -1) < view.move:
            moves = travel_moves(view, dist[punk])
            if moves <= view.moves_left:
                deny = PUNK_DENY_SHARE if view.n_bots > 1 else 0.0
                best_rate = DEFAULT_CONFIG.points_punk * (1 + deny) / moves
                self.job, self.punk_goal, self.path = punk, True, path_to(parent, punk)

        for pos, chance in targets.items():
            cell = view.cell(pos)
            if cell.bot not in (None, view.bot_id) or cell.item is not None:
                continue  # another bot stands on it, or the pickup lies on it (cannot dig there)
            nbrs = view.connected_neighbors(pos)
            stands = [n for d in Dir if (n := d.step(pos)) in dist]  # reachable squares next to it
            if not stands:
                continue
            stand = min(stands, key=lambda n: (dist[n], n in nbrs))  # rather not on a square to dig
            moves = travel_moves(view, dist[stand])
            left = view.moves_left - moves
            fixable = repairable(view, pos)
            if cell.surface is Surface.UNEARTHED:  # open leak: valve it off, fix, reopen, refill
                if not fixable:
                    continue
                work = repair_moves(len(nbrs), walk) - 1
                gain = leak_value(len(nbrs), points) - points["leak_found"] if left >= work else 0.0
            else:  # unearth; repair if it leaks (if it can be: else only +found), else refill
                cap = left if fixable else min(left, 2)
                gain = dig_payoff(chance, cap, points, len(nbrs), walk)
                work = dig_work(chance, cap, len(nbrs), walk)
            rate = gain / (moves + work)  # expected points per move
            if rate > best_rate:
                best_rate, self.job, self.job_chance, self.punk_goal = rate, pos, chance, False
                self.path = path_to(parent, stand)
        if self.job is not None:
            self.next_plan = 0

