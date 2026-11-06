"""TenchijinBot: earn 25 points, buy the Tenchijin satellite, then dig up the riskiest pipes.

Strategy, in three phases:
1. Explore: follow pipes and scan with detect (+1 per new pipe square) until the bot can
   afford the satellite, then buy it.
2. Hunt: the satellite grades every pipe square A..E. Visit the untouched D and E squares,
   best "expected points per move" first (so a near D can beat a far E). Unearth the square
   from a neighbour, fix it if it leaks, then refill the hole.
3. Leftovers: try C squares the same way, fix leaks other bots left open, else detect
   (always a valid move, so idling never counts as an invalid move).

Run it:    python main.py --bots tenchijin_bot:TenchijinBot
Benchmark: python simulate.py --bots tenchijin_bot --rounds 100 --seed 1
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterator, Mapping

from leakbots import DEFAULT_CONFIG, Action, BaseBot, BotView, Dir, PipeState, Pos, PowerUp, Surface
from leakbots.model import RISK_GRADES

SATELLITE = PowerUp.SATELLITE
PRIMARY_GRADES = ("D", "E")  # phase 2 digs these grades
BONUS_MIN_LEAK_CHANCE = 0.01  # phase 3 also digs grades at least this likely to leak (C)
MIN_MOVES_TO_BUY = 40  # with fewer moves left the satellite rarely pays back its price
IDLE_REPLAN_EVERY = 25  # moves between target searches while there is nothing to do

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


def dig_payoff(chance: float, moves_left: int, points: Mapping[str, int]) -> float:
    """Expected points of unearthing a pipe square now, given the moves left to fix and refill it."""
    found, fixed, penalty = points["leak_found"], points["leak_fixed"], points["open_hole_penalty"]
    if moves_left >= 3:  # unearth, fix if leaking, refill
        return chance * (found + fixed)
    if moves_left == 2:  # unearth + fix (the hole stays open) or unearth + refill
        return chance * (found + fixed - penalty)
    if moves_left == 1:  # unearth only: the hole stays open either way
        return chance * found - penalty
    return 0.0


def bfs(view: BotView, start: Pos, parent: dict[Pos, Pos | None]) -> Iterator[tuple[Pos, int]]:
    """Yield (square, distance) over walkable squares, nearest first. Fills parent for paths."""
    parent[start] = None
    queue = deque([(start, 0)])
    while queue:
        pos, dist = queue.popleft()
        yield pos, dist
        for d in Dir:
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


class TenchijinBot(BaseBot):
    name = "TenchijinBot"

    def on_game_start(self, view: BotView) -> None:
        self.my_holes: set[Pos] = set()  # squares this bot dug that are still open
        self.leak_chance: dict[str, float] = {}  # P(leak | grade), filled once the satellite arrives
        self.goal: Pos | None = None  # square to dig up (or a leak to fix)
        self.goal_chance = 0.0  # its chance to leak
        self.path: list[Pos] = []  # squares still to walk to stand next to the goal
        self.next_plan = 0  # earliest move for the next target search while idle
        self.bought_at: int | None = None  # move at which the satellite was bought

    def act(self, view: BotView) -> Action:
        # Own holes first: fix the leak, then refill, so no hole stays open at the end
        self.my_holes = {p for p in self.my_holes if view.surface(p) is Surface.UNEARTHED}
        for hole in sorted(self.my_holes):
            d = direction(view.me, hole)  # always next to us: we finish a hole before moving on
            if d is not None:
                broken = view.cell(hole).pipe_state is PipeState.BROKEN
                return Action.fix(d) if broken else Action.refill(d)

        # Phase 1 -> 2: buy the satellite as soon as it is affordable (and still worth it)
        if self._should_buy(view):
            self.bought_at = view.move
            self.goal, self.path, self.next_plan = None, [], 0
            return Action.buy(SATELLITE)

        # Targets: D/E squares (C later) once we own the satellite; in every phase, leaks
        # that other bots dug up and left open (+15 for whoever fixes them)
        action = self._hunt(view)
        if action is not None:
            return action
        if not view.has(SATELLITE):
            return self._explore(view)
        return Action.detect()  # nothing left to do: detect is always valid (no points now)

    # --- Phase 1: explore -------------------------------------------------------------------
    def _should_buy(self, view: BotView) -> bool:
        return (
            not view.has(SATELLITE)
            and view.score >= view.cost(SATELLITE)
            and view.moves_left >= MIN_MOVES_TO_BUY
        )

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
        for pos, dist in bfs(view, view.me, parent):
            if dist == 0:
                continue
            if MAX_SQUARE_VALUE / (dist + 1) < best[0]:
                break  # nothing farther away can beat the best rate
            value = scan_gain(pos) + (chance(pos) if unknown(pos) else 0.0)
            candidate = key(value / (dist + 1), pos)  # +1: the detect once we get there
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

    # --- Phase 2/3: hunt ----------------------------------------------------------------------
    def _hunt(self, view: BotView) -> Action | None:
        """Walk to the best target and dig it up (or fix a leak another bot left open)."""
        if SATELLITE in view.upgrades and not self.leak_chance:
            n_e = sum(1 for r in view.satellite.values() if r.risk == RISK_GRADES[-1])
            # The view does not tell how many leaks a round has: use the default, at least the E count
            n_leaks = max(DEFAULT_CONFIG.n_leaks, n_e)
            self.leak_chance = leak_chances(len(view.satellite), n_leaks, DEFAULT_CONFIG.risk_decay)

        if not self._plan_still_ok(view):
            if self.goal is None and view.move < self.next_plan:
                return None  # searched recently and found nothing
            self._plan(view)
            if self.goal is None:
                self.next_plan = view.move + (IDLE_REPLAN_EVERY if view.has(SATELLITE) else 1)
                return None

        d = direction(view.me, self.goal)
        if d is None:  # not there yet: next step of the path
            return Action.move(direction(view.me, self.path.pop(0)))
        goal, self.goal = self.goal, None
        if view.surface(goal) is Surface.UNEARTHED:
            # Another bot's open leak: fix it (+15). Its open-hole penalty stays with the digger.
            return Action.fix(d)
        if dig_payoff(self.goal_chance, view.moves_left, view.points) <= 0:
            return None  # too late in the round to dig this one
        self.my_holes.add(goal)
        return Action.unearth(d)

    def _plan_still_ok(self, view: BotView) -> bool:
        if self.goal is None:
            return False
        cell = view.cell(self.goal)
        if cell.surface is Surface.UNEARTHED:
            valid = cell.pipe_state is PipeState.BROKEN  # a leak someone else dug up
        else:
            valid = not cell.revealed and cell.bot in (None, view.bot_id)  # still untouched
        if not valid:
            return False
        if direction(view.me, self.goal) is not None:
            return True  # next to the goal: work on it
        # Otherwise the next path square must be a free neighbour (other bots may block it)
        nxt = self.path[0] if self.path else None
        return nxt is not None and direction(view.me, nxt) is not None and view.is_walkable(nxt)

    def _targets(self, view: BotView) -> dict[Pos, float]:
        """Squares worth working on -> chance to leak (1.0 for a leak already dug open)."""
        targets = {}
        if view.n_bots > 1:  # only other bots leave leaks open (we fix ours right away)
            for y in range(view.height):
                for x in range(view.width):
                    pos = (x, y)
                    if view.surface(pos) is Surface.UNEARTHED and pos not in self.my_holes:
                        if view.cell(pos).pipe_state is PipeState.BROKEN:
                            targets[pos] = 1.0
        if view.has(SATELLITE):
            # Untouched pipe squares nobody has dug up yet, with their leak chance
            fresh = {p: r.risk for p, r in view.satellite.items() if not view.cell(p).revealed}
            chance = {p: self.leak_chance[grade] for p, grade in fresh.items()}
            primary = {p: c for p, c in chance.items() if fresh[p] in PRIMARY_GRADES}
            bonus = {p: c for p, c in chance.items() if c >= BONUS_MIN_LEAK_CHANCE}
            targets.update(primary or bonus)  # phase 3 (C squares) only once D and E are done
        return targets

    def _plan(self, view: BotView) -> None:
        """Pick the target with the most expected points per move (travel + work)."""
        self.goal, self.path = None, []
        targets = self._targets(view)
        if not targets:
            return
        parent: dict[Pos, Pos | None] = {}
        dist = dict(bfs(view, view.me, parent))
        points, best_rate = view.points, 0.0
        for pos, chance in targets.items():
            cell = view.cell(pos)
            if cell.bot not in (None, view.bot_id):
                continue  # another bot stands on it
            stands = [n for d in Dir if (n := d.step(pos)) in dist]  # reachable squares next to it
            if not stands:
                continue
            stand = min(stands, key=dist.__getitem__)
            if cell.surface is Surface.UNEARTHED:  # open leak: walk there and fix
                gain = points["leak_fixed"] if dist[stand] < view.moves_left else 0
                work = 1.0
            else:  # walk, unearth, fix if it leaks, refill
                gain = dig_payoff(chance, view.moves_left - dist[stand], points)
                work = 2.0 + chance
            rate = gain / (dist[stand] + work)  # expected points per move
            if rate > best_rate:
                best_rate, self.goal, self.goal_chance = rate, pos, chance
                self.path = path_to(parent, stand)
