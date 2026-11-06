"""TenchijinBot: earn 25 points, buy the Tenchijin satellite, then dig up the riskiest pipes.

Strategy, in three phases:
1. Explore: follow pipes and scan with detect (+1 per new pipe square) until the bot can
   afford the satellite, then buy it.
2. Hunt: the satellite grades every pipe square A..E. Visit the untouched D and E squares,
   best "expected points per move" first (so a near D can beat a far E). Unearth the square
   from a neighbour, fix it if it leaks, then refill the hole.
3. Leftovers: try C squares the same way, fix leaks other bots left open, else detect
   (always a valid move, so idling never counts as an invalid move).

Punk upgrade (pickup from move 200): the pickup is one more target in the same "expected points
per move" scoring, so the bot detours for it when it is close, and almost always once the
satellite targets run out. As a punk it chains the strongest adjacent opponent. While chained
itself a step costs two moves: the bot keeps its path until it has really moved, and prices
travel at two moves per step.

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
# The punk pickup pays points_punk. With opponents, taking it also denies them those points and
# the ball and chain they could put on us: counted as this share of the pickup on top (a guess).
PUNK_DENY_SHARE = 0.5

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


def travel_moves(view: BotView, steps: int) -> int:
    """Moves needed to walk `steps` squares: two per step while the ball and chain lasts.

    Chained for c more moves, the first ceil(c / 2) steps need a drag each (an odd last chained
    move drags, the chain wears off, then the step is free). Ignores a drag already made.
    """
    return steps + min(steps, (view.chained_moves() + 1) // 2)


class TenchijinBot(BaseBot):
    name = "TenchijinBot"

    def on_game_start(self, view: BotView) -> None:
        self.my_holes: set[Pos] = set()  # squares this bot dug that are still open
        self.leak_chance: dict[str, float] = {}  # P(leak | grade), filled once the satellite arrives
        self.goal: Pos | None = None  # square to dig up, a leak to fix, or the punk pickup
        self.goal_chance = 0.0  # its chance to leak
        self.punk_goal = False  # the goal is the punk pickup: walk onto it instead of digging
        self.path: list[Pos] = []  # squares still to walk (to stand next to the goal / onto the pickup)
        self.next_plan = 0  # earliest move for the next target search while idle
        self.punk_seen: Pos | None = None  # pickup square at the last search (it appears / vanishes)
        self.bought_at: int | None = None  # move at which the satellite was bought

    def act(self, view: BotView) -> Action:
        # Own holes first: fix the leak, then refill, so no hole stays open at the end
        self.my_holes = {p for p in self.my_holes if view.surface(p) is Surface.UNEARTHED}
        for hole in sorted(self.my_holes):
            d = direction(view.me, hole)  # always next to us: we finish a hole before moving on
            if d is not None:
                broken = view.cell(hole).pipe_state is PipeState.BROKEN
                return Action.fix(d) if broken else Action.refill(d)

        # Punk: the ball and chain is a one-off chance whenever an opponent stands next to us,
        # so it goes before everything except finishing our own open hole (handled above)
        victim = self._chain_target(view)
        if victim is not None:
            return Action.chain(victim)

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

    @staticmethod
    def _chain_target(view: BotView) -> Dir | None:
        """As a punk: direction of the strongest adjacent opponent that is not chained yet."""
        if not view.can_chain:
            return None
        victims = []  # (score, -id, direction): highest score first, ties to the lower id
        for d in Dir:
            pos = d.step(view.me)
            bot = view.cell(pos).bot if view.in_bounds(pos) else None
            if bot is not None and bot != view.bot_id and view.chained_moves(bot) == 0:
                victims.append((view.scores[bot], -bot, d))
        return max(victims)[2] if victims else None

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

    # --- Phase 2/3: hunt ----------------------------------------------------------------------
    def _hunt(self, view: BotView) -> Action | None:
        """Walk to the best target and dig it up (or fix a leak another bot left open)."""
        if SATELLITE in view.upgrades and not self.leak_chance:
            n_e = sum(1 for r in view.satellite.values() if r.risk == RISK_GRADES[-1])
            # The view does not tell how many leaks a round has: use the default, at least the E count
            n_leaks = max(DEFAULT_CONFIG.n_leaks, n_e)
            self.leak_chance = leak_chances(len(view.satellite), n_leaks, DEFAULT_CONFIG.risk_decay)

        if view.punk_pos != self.punk_seen:  # the pickup appeared or was taken: search again now
            self.punk_seen, self.next_plan = view.punk_pos, 0
        if not self._plan_still_ok(view):
            if self.goal is None and view.move < self.next_plan:
                return None  # searched recently and found nothing
            self._plan(view)
            if self.goal is None:
                self.next_plan = view.move + (IDLE_REPLAN_EVERY if view.has(SATELLITE) else 1)
                return None

        if self.path:
            # Not there yet. The path square is only dropped once we stand on it: while chained
            # the first MOVE merely drags, and repeating it in the same direction steps.
            return Action.move(direction(view.me, self.path[0]))
        goal, self.goal = self.goal, None
        d = direction(view.me, goal)
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
        if view.me in self.path:  # drop the squares we have really walked
            del self.path[: self.path.index(view.me) + 1]
        if self.punk_goal:
            valid = view.punk_pos == self.goal  # nobody has taken the pickup yet
        else:
            cell = view.cell(self.goal)
            if cell.surface is Surface.UNEARTHED:
                valid = cell.pipe_state is PipeState.BROKEN  # a leak someone else dug up
            else:  # still untouched, no other bot on it, no pickup lying on it (blocks digging)
                valid = not cell.revealed and cell.bot in (None, view.bot_id) and cell.item is None
        if not valid:
            return False
        if not self.path:
            return direction(view.me, self.goal) is not None  # next to the goal: work on it
        # Otherwise the next path square must be a free neighbour (other bots may block it).
        # A drag toward another square is simply dropped by the next MOVE (no extra cost).
        nxt = self.path[0]
        return direction(view.me, nxt) is not None and view.is_walkable(nxt)

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
        """Pick the target with the most expected points per move (travel + work).

        Travel is priced with travel_moves (two moves per step while chained), so a chained bot
        prefers near targets and never starts a dig it cannot finish before the round ends.
        """
        self.goal, self.path, self.punk_goal = None, [], False
        targets = self._targets(view)
        punk = view.punk_pos
        if not targets and punk is None:
            return
        parent: dict[Pos, Pos | None] = {}
        dist = dict(bfs(view, view.me, parent, view.dragging))  # ties keep a half-dragged step
        points, best_rate = view.points, 0.0

        # Punk pickup: points_punk (more with opponents, who would otherwise get it) for walking
        # onto it, no work after. Scored like any target, so it is a detour only when it is close:
        # e.g. it beats an E leak 10 steps away (20 / 13 = 1.5 points per move) within ~9 steps,
        # but most D squares (~1 in 4 leaks) from much farther. Once the satellite targets run
        # out the bot only detects for 0 points, so then any pickup it can reach in time wins.
        if punk is not None and punk in dist:
            moves = travel_moves(view, dist[punk])
            if moves <= view.moves_left:
                deny = PUNK_DENY_SHARE if view.n_bots > 1 else 0.0
                best_rate = DEFAULT_CONFIG.points_punk * (1 + deny) / moves
                self.goal, self.punk_goal, self.path = punk, True, path_to(parent, punk)

        for pos, chance in targets.items():
            cell = view.cell(pos)
            if cell.bot not in (None, view.bot_id) or cell.item is not None:
                continue  # another bot stands on it, or the pickup lies on it (cannot dig there)
            stands = [n for d in Dir if (n := d.step(pos)) in dist]  # reachable squares next to it
            if not stands:
                continue
            stand = min(stands, key=dist.__getitem__)
            moves = travel_moves(view, dist[stand])
            if cell.surface is Surface.UNEARTHED:  # open leak: walk there and fix
                gain = points["leak_fixed"] if moves < view.moves_left else 0
                work = 1.0
            else:  # walk, unearth, fix if it leaks, refill
                gain = dig_payoff(chance, view.moves_left - moves, points)
                work = 2.0 + chance
            rate = gain / (moves + work)  # expected points per move
            if rate > best_rate:
                best_rate, self.goal, self.goal_chance, self.punk_goal = rate, pos, chance, False
                self.path = path_to(parent, stand)
