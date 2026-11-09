"""BossBot: an example competitor that plays for the most points, and makes its rivals pay.

Strategy (each switch below was measured; ablation numbers: 15-20 rounds per seat order, seed 2000+):
    Hindering (the decisive part)
      Lock      Right after spawning it walks to the waterworks (水道局) and digs every square around
                it. The example rivals never refill someone else's hole, so they can no longer buy
                UT or the satellite (they managed it in 2 of 60 rounds). When BossBot can afford
                its own satellite it refills one lock hole, buys, and digs it again on the way out.
                Holes without pipe cost nothing; the lock's pipe holes are refilled in the last
                LOCK_RELEASE_LEFT moves (-2 each otherwise). Single player: no lock.
                Effect: vs TenchijinBot 40 % -> 97 % wins, battle royale 47 % -> 87 %.
                Counter for competitors: refill a hole around the waterworks (any bot may refill
                any hole) and step in.
      Steal     Any leak whose connected squares all hold closed valves is fixed on sight, also
                when an opponent prepared it; closed valves next to fixed pipes are reopened (+5).
                It never unearths its own prepared leak while an opponent stands next to it.
      Punk      The pickup (move 200) is a target worth its 10 points plus 10 for denying it.
      Chain     As a punk it chains an adjacent opponent at once, and chases one that is close.
      Trap      If an opponent has a single free exit that is a known non-pipe square (no
                penalty), it digs it.
    Point engine
      Explore   Follow the pipe network with steps and detects (+1 per new pipe square), aiming at
                the squares with the best expected points per move.
      Satellite Bought as soon as a rival could be racing for the leaks (else late, SATELLITE_LEFT
                moves before the end, to keep earning detect points first). UT is never bought:
                with the satellite it only cost points (ablation).
      Repair    A leak is worth 5 (found) + 40 (fix) + 5 per valve reopened. The bot stands ON the
                still-buried leak (it touches every square to valve off), digs, valves and closes
                each connected neighbour, steps aside, unearths and fixes the leak in two moves
                (a short window for thieves), refills it, steps back on it and reopens + refills
                every valve.
      Probe     D/C squares are dug only while a leak is still unaccounted for (no inspection loop).
    Safety
      Every plan must fit into the moves left; in the last moves the bot reopens its closed
      valves (-10 each otherwise) and refills its pipe holes (-2 each). Blocked by holes, it
      refills a non-pipe hole to get through (never its own lock, except to shop).

Run it:    python main.py --bots boss_bot government_bot tenchijin_bot
Benchmark: python simulate.py --bots boss_bot tenchijin_bot --rounds 100 --seed 1
"""

from __future__ import annotations

import heapq

from leakbots import Action, ActionType, BaseBot, BotView, Dir, PipeState, PowerUp, Surface
from leakbots.model import RISK_GRADES

UT, SATELLITE = PowerUp.UT, PowerUp.SATELLITE

# Chance that an undetected square holds pipe, from detected neighbours (pipes run straight)
P_BASE = 0.25  # nothing known next to it (about 28 % of all squares hold pipe)
P_STRAIGHT = 0.8  # continues a straight line of two pipe squares
P_TURN = 0.5  # beside a pipe end that cannot run straight on
P_SIDE = 0.15  # beside a pipe that probably runs straight on
P_JUNCTION = 0.1  # beside a pipe whose two links are already known
P_MAX = 0.9
PIPE_SHARE = 0.29  # rough share of pipe squares on a map (to estimate unchecked pipe)

LEAK_WORTH = 40.0  # explorer: what finding a leak is worth beyond the moves its repair takes
FULL_SCAN_EVERY = 25  # moves between full reads of the public map (changes happen next to bots)
SWITCH_FACTOR = 1.3  # a new job must be this much better per move to replace an unstarted one
SAFETY = 3  # spare moves kept when planning the last jobs of a round
BUY_RATE = 100.0  # a wanted power-up beats every other job once it is affordable
MIN_LEFT_TO_BUY = 60  # with fewer moves left a power-up rarely pays back
PUNK_DENY = 10.0  # extra worth of the pickup with opponents (they would get +10 and a chain)
CHAIN_WORTH = 12.0  # job value of chasing an opponent to chain it (it loses ~half its speed)
CHASE_RANGE = 8  # chase only opponents this close (moves)
STEAL_WAIT = 8  # moves to wait for an opponent to leave a prepared leak before going ahead
STUCK_MOVES = 40  # a job that only walks this long is dropped for as many moves
LOCK_MIN_LEFT = 60  # lock the waterworks only while rivals could still profit from a purchase
LOCK_RELEASE_LEFT = 40  # from here on, refill the lock's pipe holes (else -2 each at the end)
LOCK_RANGE = 8  # (re)lock only from nearby: never cross the map for it
GUARD_MOVES = 30  # most moves spent holding the waterworks while a rival stands next to it


def leak_chances(n_pipes: int, n_leaks: int, decay: float = 0.3) -> dict[str, float]:
    """P(leak | satellite grade): intact pipes draw A..D, leaks E..B, with P(level k) ~ decay**k."""
    levels = len(RISK_GRADES) - 1
    total = sum(decay**k for k in range(levels))
    weight = [decay**k / total for k in range(levels)]
    chances = {}
    for i, grade in enumerate(RISK_GRADES):
        intact = max(n_pipes - n_leaks, 0) * weight[i] if i < levels else 0.0
        leaky = n_leaks * weight[levels - i] if i > 0 else 0.0
        chances[grade] = leaky / (intact + leaky) if intact + leaky else 0.0
    return chances


class BossBot(BaseBot):
    name = "BossBot"

    # Strategy switches (the ablation runs flip them in subclasses)
    BUY_UT = False
    # "adaptive": late, but at once when an opponent owns a leak finder (a race for the leaks);
    # "early": as soon as affordable; "late": SATELLITE_LEFT moves before the end; "never"
    SATELLITE = "adaptive"
    SATELLITE_LEFT = 300
    STEAL = True  # fix leaks others prepared, reopen their valves, guard our own prepared leaks
    PUNK = True
    CHAIN = True  # chain adjacent opponents
    CHASE = True  # walk to a near opponent to chain it
    TRAP = True  # dig an opponent's last free exit
    LOCK_SHOP = True  # dig every square around the waterworks: rivals cannot shop any more
    LOCK_EARLY = True  # lock right from the start (before rivals can afford anything), unlock to buy

    # --- Setup -----------------------------------------------------------------------------
    def on_game_start(self, view: BotView) -> None:
        w, h = view.width, view.height
        self.w = w
        n = w * h
        self.pos = [(i % w, i // w) for i in range(n)]
        # Flat indices: neighbours as (direction, index), and the neighbour in each direction
        self.adj: list[list[tuple[Dir, int]]] = []
        self.step: list[dict[Dir, int]] = []
        for p in self.pos:
            nb = [(d, q[1] * w + q[0]) for d in Dir if view.in_bounds(q := d.step(p))]
            self.adj.append(nb)
            self.step.append(dict(nb))
        self.nbrs = [[j for _, j in nb] for nb in self.adj]
        self.known = [-1] * n  # pipe knowledge: -1 unknown, 0 no pipe, 1 pipe
        self.disc = bytearray(n)  # 1 = cannot pay +1 to this bot any more
        self.hole = bytearray(n)  # unearthed right now (blocks walking)
        self.state: dict[int, PipeState] = {}  # revealed pipes: OK / BROKEN / FIXED
        self.valve: dict[int, bool] = {}  # valve open (True) / closed (False), kept while buried
        self.leaks: set[int] = set()  # squares known to leak right now
        self.seen_leaks: set[int] = set()  # every square ever known to leak (for counting)
        self.checked: dict[int, int] = {}  # UT reading "no leak": square -> move of the reading
        self.grade: dict[int, str] = {}  # satellite grades
        self.chance: dict[str, float] = {}  # P(leak | grade)
        self.suspects: list[int] = []  # satellite squares graded D or C
        self.sat_age: int | None = None
        self.sat_move = -1  # move of the latest satellite image
        self.my_holes: set[int] = set()  # holes this bot dug that are still open
        self.my_closed: set[int] = set()  # valves this bot closed that are still closed
        self.occ: dict[int, int] = {}  # square -> other bot standing there
        self.pipe_feed: dict[int, list[int]] = {}  # known pipe courses: square -> connected squares
        self.job: tuple[str, int] | None = None
        self.scan_move = -FULL_SCAN_EVERY  # forces a full map read on the first move
        self.leaks_total = view.leaks_total
        self.last_spawn = 0  # move at which the latest leak appeared
        self.last_job: tuple[str, int] | None = None
        self.progress = 0  # move of the last action that was not a step
        self.banned: dict[tuple[str, int], int] = {}  # job -> move until which it is ignored
        self.waited = 0  # moves spent waiting for an opponent to leave a prepared leak
        ww = view.waterworks_pos
        self.ww = None if ww is None else self.idx(ww)
        self.lock_sq = set() if self.ww is None else {j for _, j in self.adj[self.ww]}  # around the shop
        self.lock_holes: set[int] = set()  # lock holes we dug: kept open on purpose, not "work"
        self.lock_digging: int | None = None  # square our last unearth targeted for the lock
        self.locked_once = False  # the first full lock is done (later only re-lock from nearby)
        self.guarded = 0  # moves spent holding the waterworks square against a rival next to it
        for pos, has_pipe in view.detected.items():
            self._learn(self.idx(pos), has_pipe)

    def idx(self, pos: tuple[int, int]) -> int:
        return pos[1] * self.w + pos[0]

    # --- Move ------------------------------------------------------------------------------
    def act(self, view: BotView) -> Action:
        self._sync(view)
        # A job that only walks for STUCK_MOVES moves (blocked, oscillating) is dropped for a while
        if self.job != self.last_job:
            self.last_job, self.progress = self.job, self.move
        elif self.job is not None and self.move - self.progress > STUCK_MOVES:
            self.banned[self.job] = self.move + STUCK_MOVES
            self.job = None
        action = self._decide(view) or Action.detect()  # detect is always valid
        if action.type is not ActionType.MOVE:
            self.progress = self.move
        return action

    def _decide(self, view: BotView) -> Action | None:
        # 1. A fixable leak next to us (ours, or one an opponent prepared): +40 now
        action = self._adjacent_fix(view)
        if action is not None:
            return action
        # 2. Last moves of the round: reopen own closed valves, refill own pipe holes
        if self._cleanup_due(view):
            action = self._cleanup(view)
            if action is not None:
                return action
        # 3. Hindering: chain an adjacent opponent, dig an opponent's last exit
        action = self._chain(view) or self._trap(view)
        if action is not None:
            return action
        # 4. Buy a wanted power-up while standing at the waterworks
        want = self._want(view)
        if want is not None and (self.ww is None or self.me == self.ww) and not self._started():
            self.job = None
            return Action.buy(want)
        # 4b. Shopping done: lock the waterworks so no rival can buy UT or the satellite any more
        action = self._lock(view, want)
        if action is not None:
            return action
        # 5. Jobs: repair, probe, steal, reopen, punk, buy, chase; else explore
        return self._work_on_jobs(view, want)

    # --- Memory ----------------------------------------------------------------------------
    def _learn(self, i: int, has_pipe: bool) -> None:
        self.known[i] = 1 if has_pipe else 0
        self.disc[i] = 1

    def _sync(self, view: BotView) -> None:
        """Update private memory from the previous move's result and the public map."""
        self.me = self.idx(view.me)
        self.move, self.left = view.move, view.moves_left
        self.chained = view.chained_moves()
        self.dragging = view.dragging
        self._dist: list[int] | None = None
        self._feeds: dict[int, list[int] | None] = {}
        positions = view.bot_positions()
        self.occ = {self.idx(p): b for b, p in positions.items() if b != view.bot_id}
        if view.leaks_total != self.leaks_total:
            self.leaks_total, self.last_spawn = view.leaks_total, self.move

        result = view.last_result
        if result is not None:
            done = result.action
            if result.success and done is not None and done.direction is not None:
                t = self.idx(done.direction.step(view.me))  # these actions do not move the bot
                kind = done.type
                if kind is ActionType.UNEARTH:
                    (self.lock_holes if t == self.lock_digging else self.my_holes).add(t)
                elif kind is ActionType.REFILL:
                    self.my_holes.discard(t)
                    self.lock_holes.discard(t)
                elif kind is ActionType.INSTALL_VALVE:
                    self.valve[t] = True
                elif kind is ActionType.CLOSE_VALVE:
                    self.my_closed.add(t)
                    self.valve[t] = False
                elif kind is ActionType.OPEN_VALVE:
                    self.my_closed.discard(t)
                    self.valve[t] = True
            elif done is not None and done.type is ActionType.FIX and done.direction is not None:
                # Our valve memory was wrong (a buried valve changed unseen): forget the buried ones
                leak = done.direction.step(view.me)
                for p in view.connected_neighbors(leak):
                    if not self.hole[self.idx(p)]:
                        self.valve.pop(self.idx(p), None)
            for pos, has_pipe in (result.scan or {}).items():
                self._learn(self.idx(pos), has_pipe)
            for pos, leaking in (result.scan_leaks or {}).items():
                i = self.idx(pos)
                if leaking:
                    self.leaks.add(i)
                    self.seen_leaks.add(i)
                else:
                    self.checked[i] = self.move
                    self.leaks.discard(i)

        # Public map: holes, revealed pipes and valves only change next to a bot
        if self.move - self.scan_move >= FULL_SCAN_EVERY:
            self.scan_move = self.move
            for i, p in enumerate(self.pos):
                if self.hole[i] or view.surface(p) is Surface.UNEARTHED:
                    self._read(view, i)
        for p in positions.values():
            i = self.idx(p)
            self._read(view, i)
            for _, j in self.adj[i]:
                self._read(view, j)
        self.my_holes = {i for i in self.my_holes if self.hole[i]}
        self.lock_holes = {i for i in self.lock_holes if self.hole[i]}
        self.lock_digging = None
        self.my_closed = {i for i in self.my_closed if self.valve.get(i) is False}

        if view.has(SATELLITE):
            age = view.satellite_age
            if self.sat_age is None or age < self.sat_age:
                self._read_satellite(view)
            self.sat_age = age

    def _read(self, view: BotView, i: int) -> None:
        cell = view.cell(self.pos[i])
        is_hole = cell.surface is Surface.UNEARTHED
        self.hole[i] = is_hole
        if not cell.revealed:
            return
        self._learn(i, bool(cell.has_pipe))
        if cell.has_pipe:
            self.state[i] = cell.pipe_state
            if cell.pipe_state is not PipeState.OK:
                self.seen_leaks.add(i)  # broken or fixed: it was a leak
            if cell.pipe_state is PipeState.BROKEN:
                self.leaks.add(i)
            else:
                self.leaks.discard(i)
        if is_hole:  # a valve's position is only visible while its square is open
            if cell.valve is None:
                self.valve.pop(i, None)
            else:
                self.valve[i] = cell.valve

    def _read_satellite(self, view: BotView) -> None:
        self.sat_move = self.move
        n = len(self.pos)
        self.known = [0] * n
        self.disc = bytearray([1]) * n
        self.grade = {}
        for pos, reading in view.satellite.items():
            i = self.idx(pos)
            self.known[i] = 1
            self.grade[i] = reading.risk
        self.chance = leak_chances(len(self.grade), max(view.leaks_total, 1))
        for i, g in self.grade.items():
            if g == "E" and self.state.get(i) is not PipeState.FIXED and not self.hole[i]:
                self.leaks.add(i)
                self.seen_leaks.add(i)
        # Squares worth checking: D (about 1 in 4 leaks) and C (about 1 in 35); B is hopeless
        self.suspects = [i for i, g in self.grade.items() if g in "DC"]

    # --- Small helpers ---------------------------------------------------------------------
    def _dir(self, a: int, b: int) -> Dir | None:
        """Direction from a to its neighbour b (None if not adjacent)."""
        for d, j in self.adj[a]:
            if j == b:
                return d
        return None

    def _walkable(self, i: int) -> bool:
        return not self.hole[i] and i not in self.occ

    def _diggable(self, view: BotView, i: int) -> bool:
        if self.hole[i] or i in self.occ or i == self.me or i == self.ww:
            return False
        return view.punk_pos is None or self.idx(view.punk_pos) != i

    def _travel(self, steps: int) -> int:
        """Moves to walk `steps` squares: two per step while the ball and chain lasts."""
        return steps + min(steps, (self.chained + 1) // 2)

    def _started(self) -> bool:
        """Own holes or closed valves exist: finish that work before anything else."""
        return bool(self.my_holes or self.my_closed)

    def _feed(self, view: BotView, leak: int) -> list[int] | None:
        """Squares to valve off before fixing `leak`; None while its pipe's course is unclear."""
        feed = self.pipe_feed.get(leak)
        if feed is not None:
            return feed
        if leak in self._feeds:
            return self._feeds[leak]
        nb = view.connected_neighbors(self.pos[leak])
        if nb:  # the pipe's course is known (dug up or satellite) and never changes
            feed = self.pipe_feed[leak] = [self.idx(p) for p in nb]
            return feed
        feed = self._feeds[leak] = self._guess_feed(leak)
        return feed

    def _guess_feed(self, leak: int) -> list[int] | None:
        sides = self.adj[leak]
        if any(self.known[j] == -1 for _, j in sides):
            return None
        pipes = [j for _, j in sides if self.known[j] == 1]
        # Inside the map a pipe never dead-ends: exactly two pipe neighbours are its two links.
        # On the border one may lead off the map, so then only a single pipe neighbour is certain.
        if (len(sides) == 4 and len(pipes) == 2) or (len(sides) < 4 and len(pipes) == 1):
            return pipes
        return None

    def _fix_stand(self, leak: int, feed: list[int]) -> bool:
        """Is a square next to the leak left to stand on once every fed side is a valve hole?"""
        return any(j not in feed and not (self.hole[j] and j not in self.lock_holes) for _, j in self.adj[leak])

    def _opponent_next_to(self, i: int) -> bool:
        return any(j in self.occ for _, j in self.adj[i])

    # --- Pathfinding -----------------------------------------------------------------------
    def _bfs(self) -> list[int]:
        """Distances over walkable squares from the bot (cached per move); fills self._first."""
        if self._dist is not None:
            return self._dist
        n = len(self.pos)
        dist, first = [-1] * n, [None] * n
        me = self.me
        dist[me] = 0
        blocked = bytearray(self.hole)  # holes and other bots block walking
        for o in self.occ:
            blocked[o] = 1
        order = [me]
        start = self.adj[me]
        if self.dragging is not None:  # ties keep a half-dragged step (chained)
            start = sorted(start, key=lambda s: s[0] is not self.dragging)
        for d, j in start:
            if not blocked[j]:
                dist[j], first[j] = 1, d
                order.append(j)
        nbrs = self.nbrs
        for i in order:  # the list grows while we walk it: breadth-first order
            if i == me:
                continue
            di, fi = dist[i] + 1, first[i]
            for j in nbrs[i]:
                if dist[j] < 0 and not blocked[j]:
                    dist[j], first[j] = di, fi
                    order.append(j)
        self._dist, self._first, self._order = dist, first, order
        return dist

    def _go(self, goals: dict[int, float]) -> Action | None:
        """Step towards the goal square with the lowest distance + extra cost."""
        dist = self._bfs()
        best = None
        for g, extra in goals.items():
            if dist[g] > 0 and (best is None or dist[g] + extra < best[0]):
                best = (dist[g] + extra, g)
        if best is not None:
            return Action.move(self._first[best[1]])
        return self._unblock(goals)

    def _unblock(self, goals: dict[int, float]) -> Action | None:
        """No walkable path: route through holes that can be refilled (refill, then step)."""
        if not goals:
            return None
        me = self.me
        cost = {me: 0}
        parent: dict[int, int] = {}
        heap = [(0, me)]
        target = None
        while heap:
            c, i = heapq.heappop(heap)
            if c > cost.get(i, 1 << 30):
                continue
            if i in goals and i != me:
                target = i
                break
            for _, j in self.adj[i]:
                if j in self.occ:
                    continue
                if self.hole[j]:
                    if self.state.get(j) is PipeState.BROKEN or self.valve.get(j) is False:
                        continue  # cannot refill a leak; keep closed valves of repairs dug
                    if j in self.lock_holes and self.left > LOCK_MIN_LEFT and self.ww not in goals:
                        continue  # never open our own lock, except to shop there ourselves
                    step = 2 if self.known[j] == 0 else 3  # refilling pipe helps its digger
                else:
                    step = 1
                if c + step < cost.get(j, 1 << 30):
                    cost[j] = c + step
                    parent[j] = i
                    heapq.heappush(heap, (c + step, j))
        if target is None:
            return None
        while parent[target] != me:
            target = parent[target]
        d = self._dir(me, target)
        return Action.refill(d) if self.hole[target] else Action.move(d)

    # --- Opportunistic actions -------------------------------------------------------------
    def _fixable(self, view: BotView, leak: int) -> bool:
        if not self.hole[leak] or self.state.get(leak) is not PipeState.BROKEN:
            return False
        feed = view.connected_neighbors(self.pos[leak])
        return bool(feed) and all(self.valve.get(self.idx(p)) is False for p in feed)

    def _adjacent_fix(self, view: BotView) -> Action | None:
        for d, j in self.adj[self.me]:
            if j in self.leaks and self._fixable(view, j):
                if self.STEAL or j in self.my_holes or self._mine_prepared(view, j):
                    return Action.fix(d)
        return None

    def _mine_prepared(self, view: BotView, leak: int) -> bool:
        return any(self.idx(p) in self.my_closed for p in view.connected_neighbors(self.pos[leak]))

    def _chain(self, view: BotView) -> Action | None:
        """As a punk: chain the strongest adjacent opponent that is not chained yet."""
        if not self.CHAIN or not view.can_chain:
            return None
        victims = [
            (view.scores[b], -b, d)
            for d, j in self.adj[self.me]
            if (b := self.occ.get(j)) is not None and view.chained_moves(b) == 0
        ]
        return Action.chain(max(victims)[2]) if victims else None

    def _trap(self, view: BotView) -> Action | None:
        """Dig an opponent's last free exit when that costs us nothing but this move.

        Only known non-pipe squares (no hole penalty), and only when we do not stand next to the
        opponent ourselves (our own square would free it again as soon as we walk away).
        """
        if not self.TRAP or not self.occ or self._started():
            return None
        for b_sq in self.occ:
            if any(j == self.me for _, j in self.adj[b_sq]):
                continue
            exits = [j for _, j in self.adj[b_sq] if self._walkable(j) and j != self.me]
            if len(exits) != 1:
                continue
            e = exits[0]
            d = self._dir(self.me, e)
            if d is not None and self.known[e] == 0 and self._diggable(view, e):
                return Action.unearth(d)
        return None

    def _lock(self, view: BotView, want: PowerUp | None) -> Action | None:
        """Dig every free square around the waterworks (from outside, never trapping ourselves).

        Rivals in this arena never refill someone else's hole, so a locked shop ends their
        plans for UT / the satellite. Squares without pipe cost nothing; the pipe ones are refilled
        in the last LOCK_RELEASE_LEFT moves (see _liabilities). Single player: pointless, skipped.
        """
        if (not self.LOCK_SHOP or self.ww is None or view.n_bots < 2 or want is not None
                or self.left <= LOCK_MIN_LEFT or self._started()):
            return None
        if not self.LOCK_EARLY and not view.has(SATELLITE):
            return None
        targets = [n for n in self.lock_sq if not self.hole[n] and n not in self.occ]
        occupied = [n for n in self.lock_sq if not self.hole[n] and n in self.occ]
        if occupied and self.guarded < GUARD_MOVES:
            # A rival stands next to the shop: hold the waterworks square ourselves (one bot per
            # square, so it cannot step in), dig what we can from there, seal the rest once it leaves
            dist = self._bfs()
            if self.me == self.ww:
                self.guarded += 1
                action = self._work(view, [(n, "unearth") for n in targets]) if targets else None
                if action is not None and action.type is ActionType.UNEARTH:
                    self.lock_digging = self.idx(action.direction.step(view.me))
                    return action
                return Action.detect()  # hold the shop (detect is always valid)
            if dist[self.ww] > 0 and (not self.locked_once or dist[self.ww] <= LOCK_RANGE):
                return self._go({self.ww: 0.0})
        if not targets:
            if not occupied:
                self.locked_once, self.guarded = True, 0
            return None
        if len(targets) >= 2:
            # Stand on the shop first (it blocks the entrance at once), dig all sides but one from
            # there, then step out through the last one and seal it from outside
            exit_sq = max(targets, key=lambda n: (sum(self._walkable(j) for j in self.nbrs[n] if j != self.ww), -n))
            if self.me == self.ww:
                for t in targets:
                    d = self._dir(self.me, t)
                    if t != exit_sq and d is not None and self._diggable(view, t):
                        self.lock_digging = t
                        return Action.unearth(d)
            elif not self.locked_once and self._bfs()[self.ww] > 0:
                return self._go({self.ww: 0.0})
        dist = self._bfs()
        near = min((dist[n] for n in targets if dist[n] >= 0), default=LOCK_RANGE + 1)
        if self.locked_once and near > LOCK_RANGE and self.me != self.ww:
            return None  # re-lock only from nearby
        action = self._work(view, [(n, "unearth") for n in targets])
        if action is not None and action.type is ActionType.UNEARTH:
            self.lock_digging = self.idx(action.direction.step(view.me))
        return action

    # --- End of the round ------------------------------------------------------------------
    def _liabilities(self) -> list[tuple[int, str]]:
        """Own closed valves and pipe holes that would cost points at the end of the round."""
        tasks = []
        for i in self.my_closed:
            tasks.append((i, "open" if self.hole[i] else "unearth"))
        for i in self.my_holes:
            if self.known[i] == 1 and self.state.get(i) is not PipeState.BROKEN and i not in self.my_closed:
                tasks.append((i, "refill"))
        if self.lock_holes and self.ww is not None:
            # The lock's pipe holes cost -2 each: refill them at the end, leaving early enough to
            # walk back to the waterworks (at most twice the distance while chained)
            x, y = self.pos[self.me]
            wx, wy = self.pos[self.ww]
            if self.left <= LOCK_RELEASE_LEFT + self._travel(abs(x - wx) + abs(y - wy)):
                tasks.extend((i, "refill") for i in self.lock_holes if self.known[i] == 1)
        return tasks

    def _cleanup_due(self, view: BotView) -> bool:
        tasks = self._liabilities()
        if not tasks:
            return False
        # Rough tour: nearest-first by Manhattan distance, plus the actions themselves
        x, y = self.pos[self.me]
        todo = [self.pos[i] for i, _ in tasks]
        moves = 0
        while todo:
            k = min(range(len(todo)), key=lambda t: abs(todo[t][0] - x) + abs(todo[t][1] - y))
            tx, ty = todo.pop(k)
            moves += max(0, abs(tx - x) + abs(ty - y) - 1) * 2 + 2
            x, y = tx, ty
        return self.left <= self._travel(moves) + SAFETY

    def _cleanup(self, view: BotView) -> Action | None:
        tasks = self._liabilities()
        # A buried closed valve: unearth, open (and refill if time allows)
        return self._work(view, tasks) if tasks else None

    # --- Power-ups -------------------------------------------------------------------------
    def _want(self, view: BotView) -> PowerUp | None:
        """The power-up to buy now, if any (and affordable)."""
        if self.left < MIN_LEFT_TO_BUY:
            return None
        if self.BUY_UT and not view.has(UT) and view.score >= view.cost(UT):
            if not (self.SATELLITE == "early" and not view.has(SATELLITE)):
                return UT
        if not view.has(SATELLITE) and view.score >= view.cost(SATELLITE):
            mode = self.SATELLITE
            if mode == "adaptive":
                mode = "early" if self._race(view) else "late"
            if mode == "early" or (mode == "late" and self.left <= self.SATELLITE_LEFT):
                return SATELLITE
        if self.BUY_UT and self.SATELLITE == "early" and view.has(SATELLITE) and not view.has(UT):
            return UT if view.score >= view.cost(UT) + 10 else None
        return None

    RACE_TRIGGER = "score"  # "upgrade": a rival owns UT/satellite; "score": a rival could buy one; "always"

    def _race(self, view: BotView) -> bool:
        """Is an opponent able to hunt leaks? Then every leak is a race and the satellite pays now."""
        for b in range(view.n_bots):
            if b == view.bot_id:
                continue
            ups = view.upgrades_of(b)
            if self.RACE_TRIGGER == "always" or UT in ups or SATELLITE in ups:
                return True
            if self.RACE_TRIGGER == "score" and view.scores[b] >= min(view.cost(UT), view.cost(SATELLITE)):
                return True
        return False

    # --- Jobs ------------------------------------------------------------------------------
    def _work_on_jobs(self, view: BotView, want: PowerUp | None) -> Action | None:
        started = self._started()
        if started and self.job is not None:  # work in progress: no need to look around
            action = self._run(view, self.job)
            if action is not None:
                return action
            self.job = None
            started = self._started()
        cands = self._candidates(view, want)
        current = None
        if self.job is not None:
            current = next((r for r, job in cands if job == self.job), None)
            if current is None and not started:
                self.job = None  # no longer a candidate (taken, fixed, out of time)
        if self.job is None or not started:
            best = max(cands, default=None)
            explore = None
            if not started and (self.job is None or self.move % 3 == 0):
                explore = self._explore(view)  # re-checked now and then while walking to a job
            if best is not None and (current is None or best[0] > SWITCH_FACTOR * current):
                self.job = best[1]
                current = best[0]
            if explore is not None and explore[0] > (current or 0.0):
                self.job = None
                return explore[1]
        for _ in range(3):  # a finished job hands over to the next best one
            if self.job is None:
                break
            action = self._run(view, self.job)
            if action is not None:
                return action
            done = self.job
            self.job = None
            rest = [c for c in self._candidates(view, want) if c[1] != done]
            if rest and not self._started():
                self.job = max(rest)[1]
        if self._started():
            return self._cleanup(view)
        explore = self._explore(view)
        if explore is not None:
            return explore[1]
        return self._idle(view)

    def _candidates(self, view: BotView, want: PowerUp | None) -> list[tuple[float, tuple[str, int]]]:
        """Every job worth doing now, as (expected points per move, (kind, target square))."""
        dist = self._bfs()
        cands: list[tuple[float, tuple[str, int]]] = []
        if want is not None and self.ww is not None and dist[self.ww] != 0:  # (our own lock may block it:
            cands.append((BUY_RATE, ("buy", self.ww)))                     # _go then refills a lock hole)
        for leak in self.leaks:
            rate = self._repair_rate(view, leak, 1.0)
            if rate > 0:
                cands.append((rate, ("repair", leak)))
        if self.suspects and view.leaks_total > len(self.seen_leaks):  # a leak is still hiding
            for i in self.suspects:
                if i in self.leaks or i in self.state or self.hole[i] or self.checked.get(i, -1) > self.sat_move:
                    continue
                rate = self._repair_rate(view, i, self.chance.get(self.grade[i], 0.0))
                if rate > 0:
                    cands.append((rate, ("probe", i)))
        if self.STEAL:
            for i, is_open in self.valve.items():
                if not is_open and self.hole[i] and self._bonus_valve(view, i):
                    stand = self._near(i)
                    if stand is not None and self._travel(stand) + 1 + SAFETY <= self.left:
                        cands.append((5.0 / (self._travel(stand) + 1), ("reopen", i)))
        punk = view.punk_pos
        if self.PUNK and punk is not None:
            p = self.idx(punk)
            if dist[p] > 0 and self._travel(dist[p]) + SAFETY <= self.left:
                worth = 10.0 + (PUNK_DENY if self.occ else 0.0)
                cands.append((worth / self._travel(dist[p]), ("punk", p)))
        if self.CHASE and view.can_chain:
            for b_sq, b in self.occ.items():
                if view.chained_moves(b) > 0:
                    continue
                stand = self._near(b_sq)
                if stand is not None and stand <= CHASE_RANGE:
                    cands.append((CHAIN_WORTH / max(1, self._travel(stand)), ("chase", b_sq)))
        if self.banned:
            cands = [c for c in cands if self.banned.get(c[1], -1) <= self.move]
        return cands

    def _near(self, i: int) -> int | None:
        """Walking distance to the nearest free square next to i (0 if we stand next to it)."""
        dist = self._bfs()
        ds = [dist[j] for _, j in self.adj[i] if dist[j] >= 0]
        return min(ds) if ds else None

    def _bonus_valve(self, view: BotView, i: int) -> bool:
        """A closed valve next to a fixed pipe it fed, not needed by any unfixed leak."""
        fixed = False
        for _, j in self.adj[i]:
            if j in self.leaks:
                return False
            if self.state.get(j) is PipeState.FIXED and self.pos[i] in view.connected_neighbors(self.pos[j]):
                fixed = True
        return fixed

    def _repair_rate(self, view: BotView, leak: int, p: float) -> float:
        """Expected points per move of repairing `leak` (leaking with chance p) from here."""
        if p <= 0 or (leak in self.occ and not self.hole[leak]):
            return 0.0
        dist = self._bfs()
        reach = [dist[j] for _, j in self.adj[leak] if dist[j] >= 0]
        if not self.hole[leak] and dist[leak] >= 0:
            reach.append(max(dist[leak] - 1, 0))
        if not reach:
            return 0.0
        travel = self._travel(min(reach))
        feed = self._feed(view, leak)
        if feed is not None and not self._fix_stand(leak, feed):
            return 0.0  # every side gets a valve hole: nowhere left to stand for the fix
        k = len(feed) if feed is not None else 2
        work = 0
        for n in feed or ():
            if self.valve.get(n) is False:
                continue
            work += 1 if self.hole[n] and n in self.valve else 2 if self.hole[n] else 3
        if feed is None:
            work += 3 * k
        work += (0 if self.hole[leak] else 1) + 1 + 2 * k + 3  # unearth, fix, reopen+refill, refill + steps
        value = 40 + 5 * k + (0 if self.hole[leak] else 5)
        if p < 1.0:  # probe: unearth + refill (UT: a detect) first; only a leak needs the repair
            check = 1 if view.has(UT) else 2
            if travel + check + work + SAFETY > self.left:
                return 0.0
            return p * value / (travel + check + p * work)
        if travel + work + SAFETY > self.left:
            return 0.0
        if self.hole[leak] and self._opponent_next_to(leak) and not self._mine_prepared(view, leak):
            feed_done = feed is not None and all(self.valve.get(n) is False for n in feed)
            if feed_done:
                return 0.0  # the opponent next to it fixes it before we get there
        return value / (travel + work)

    def _run(self, view: BotView, job: tuple[str, int]) -> Action | None:
        kind, t = job
        if kind == "repair":
            return self._repair(view, t)
        if kind == "probe":
            return self._probe(view, t)
        if kind == "punk":
            punk = view.punk_pos
            if punk is None or self.idx(punk) != t:
                return None
            return self._go({t: 0.0})
        if kind == "buy":
            return self._go({t: 0.0}) if self._want(view) is not None and self.me != t else None
        if kind == "reopen":
            if not (self.hole[t] and self.valve.get(t) is False):
                return None
            return self._work(view, [(t, "open")])
        if kind == "chase":
            if not view.can_chain or t not in self.occ:
                return None
            goals = {j: 0.0 for _, j in self.adj[t]}
            return self._go(goals)
        return None

    # --- Repairs ---------------------------------------------------------------------------
    def _repair(self, view: BotView, leak: int) -> Action | None:
        """Next move of the full repair: valves closed, unearth + fix, reopen + refill all."""
        state = self.state.get(leak)
        if state is PipeState.FIXED:
            return self._after_fix(view, leak)
        if leak not in self.leaks:
            return None
        feed = self._feed(view, leak)
        if feed is None:  # the pipe's course is unclear: unearth the leak to see it
            return self._work(view, [(leak, "unearth")]) if not self.hole[leak] else None
        if not self._fix_stand(leak, feed):
            return None  # unfixable (e.g. a pipe junction on the edge): never start valving it
        tasks = []
        for n in feed:
            if self.valve.get(n) is False:
                continue
            if not self.hole[n]:
                tasks.append((n, "unearth"))
            else:
                tasks.append((n, "install" if n not in self.valve else "close"))
        if tasks:
            return self._work(view, tasks, prefer=None if self.hole[leak] else leak)
        if self.hole[leak]:
            return self._work(view, [(leak, "fix")])
        # Every valve is closed: unearth + fix back to back, never with an opponent next to it
        if self.STEAL and self._opponent_next_to(leak) and self.waited < STEAL_WAIT:
            self.waited += 1
            return Action.detect()
        self.waited = 0
        return self._work(view, [(leak, "unearth")])

    def _after_fix(self, view: BotView, leak: int) -> Action | None:
        """Refill the fixed leak, then reopen (+5 each) and refill the valves around it."""
        tasks = []
        if self.hole[leak] and leak in self.my_holes:
            tasks.append((leak, "refill"))  # first: the refilled leak touches every valve
        for n in view.connected_neighbors(self.pos[leak]):
            i = self.idx(n)
            if self.hole[i]:
                if self.valve.get(i) is False:
                    if not any(j in self.leaks for _, j in self.adj[i]):
                        tasks.append((i, "open"))
                elif i in self.my_holes and self.state.get(i) is not PipeState.BROKEN:
                    tasks.append((i, "refill"))  # (a leaking neighbour stays open for its own repair)
            elif i in self.my_closed:
                tasks.append((i, "unearth"))
        if not tasks:
            return None
        prefer = leak if not self.hole[leak] else None
        return self._work(view, tasks, prefer=prefer)

    def _probe(self, view: BotView, i: int) -> Action | None:
        """Check a satellite suspect: UT reading (step on / detect) or unearth + refill."""
        if i in self.leaks:
            self.job = ("repair", i)
            return self._repair(view, i)
        if i in self.state:  # dug up: intact
            return self._work(view, [(i, "refill")]) if i in self.my_holes else None
        if view.has(UT):
            if self.checked.get(i, -1) > self.sat_move:
                return None
            if self._dir(self.me, i) is not None:
                return Action.detect()
            goals = {i: 0.0} if self._walkable(i) else {}
            goals.update({j: 1.0 for _, j in self.adj[i] if self._walkable(j)})
            return self._go(goals)
        return self._work(view, [(i, "unearth")])

    def _work(self, view: BotView, tasks: list[tuple[int, str]], prefer: int | None = None) -> Action | None:
        """Do one of the tasks (square, kind) from here, else walk to a square next to one."""
        me = self.me
        tasks = [(t, k) for t, k in tasks if not (k == "refill" and self.state.get(t) is PipeState.BROKEN)]
        if not tasks:
            return None
        if prefer is not None and prefer != me and self._walkable(prefer) and self._bfs()[prefer] > 0:
            if sum(1 for t, _ in tasks if self._dir(prefer, t) is not None) >= 2:
                return Action.move(self._first[prefer])  # stand on the leak: it touches every square
        digs = {t for t, kind in tasks if kind == "unearth"}
        punk = None if view.punk_pos is None else self.idx(view.punk_pos)
        for t, kind in tasks:
            d = self._dir(me, t)
            if d is None:
                continue
            if kind == "unearth":
                if t == punk and self._walkable(t):
                    return Action.move(d)  # the pickup blocks digging: collect it first
                if not self._diggable(view, t) or not self._exit_ok(me, t, digs):
                    continue
                return Action.unearth(d)
            return {
                "install": Action.install_valve,
                "close": Action.close_valve,
                "open": Action.open_valve,
                "fix": Action.fix,
                "refill": Action.refill,
            }[kind](d)
        goals: dict[int, float] = {}
        for t, kind in tasks:
            for _, s in self.adj[t]:
                if s == me or not self._walkable(s):
                    continue
                if kind == "unearth" and not self._exit_ok(s, t, digs):
                    continue
                goals[s] = 0.0
        if not goals:
            return None
        return self._go(goals)

    def _exit_ok(self, s: int, t: int, digs: set[int]) -> bool:
        """After digging t from s, s still has a free side that is not going to be dug."""
        for _, j in self.adj[s]:
            if j != t and j not in digs and (self._walkable(j) or j == self.me):
                return True
        return False

    # --- Exploration -----------------------------------------------------------------------
    def _p_leak(self, view: BotView) -> float:
        """Chance that a pipe square not UT-checked since the last new leak is leaking."""
        unaccounted = view.leaks_total - len(self.seen_leaks)
        if unaccounted <= 0:
            return 0.0
        fresh_checks = sum(1 for m in self.checked.values() if m >= self.last_spawn)
        revealed = sum(1 for s in self.state.values() if s is not None)
        est_pipes = max(PIPE_SHARE * len(self.pos), sum(1 for k in self.known if k == 1))
        return unaccounted / max(8.0, est_pipes - fresh_checks - revealed)

    def _explore(self, view: BotView) -> tuple[float, Action] | None:
        """Step or detect where the expected points per move are highest (None: nothing to learn)."""
        if self.grade:
            return None  # the satellite showed every pipe: detects pay nothing any more
        has_ut = view.has(UT)
        p_leak = self._p_leak(view) if has_ut else 0.0
        known, disc, adj = self.known, self.disc, self.adj
        memo: list[float] = [-1.0] * len(known)

        def value(i: int) -> float:
            v = memo[i]
            if v >= 0.0:
                return v
            v = 0.0
            k = known[i]
            if k != 0 and i not in self.leaks:
                c = 1.0 if k == 1 else self._pipe_chance(i)
                if not disc[i]:
                    v = c
                if p_leak and i not in self.state and self.checked.get(i, -1) < self.last_spawn:
                    v += c * p_leak * LEAK_WORTH
            memo[i] = v
            return v

        dist = self._bfs()
        me = self.me
        best_rate, best = 0.0, None
        gain = sum(value(j) for _, j in adj[me])
        if gain > 0:
            best_rate, best = gain, Action.detect()
        top = P_MAX * 5 * (1 + p_leak * LEAK_WORTH)
        first = self._first
        for i in self._order[1:]:
            cost = self._travel(dist[i])
            if top / (cost + 1) <= best_rate:
                break  # nothing farther away can beat the best plan
            own = value(i)
            if own / cost > best_rate:
                best_rate, best = own / cost, Action.move(first[i])
            rate = (own + sum(value(j) for _, j in adj[i])) / (cost + 1)
            if rate > best_rate:
                best_rate, best = rate, Action.move(first[i])
        return None if best is None else (best_rate, best)

    def _pipe_chance(self, i: int) -> float:
        """Estimated chance that the undetected square i holds pipe, from known neighbours."""
        known = self.known
        best = None
        for d, k in self.adj[i]:
            if known[k] != 1:
                continue
            links = [e for e, m in self.adj[k] if known[m] == 1]
            beyond = self.step[k].get(d)
            if beyond is not None and known[beyond] == 1:
                c = P_STRAIGHT  # i, k and the square beyond form a line
            elif len(links) >= 2:
                c = P_JUNCTION
            elif len(links) == 1:
                ahead = self.step[k].get(links[0].opposite)
                c = P_TURN if ahead is not None and known[ahead] == 0 else P_SIDE
            else:
                open_sides = sum(1 for _, m in self.adj[k] if known[m] == -1)
                c = min(P_MAX, 2 / max(open_sides, 1))
            best = c if best is None else max(best, c)
        return P_BASE if best is None else best

    def _idle(self, view: BotView) -> Action:
        """Nothing to do: drift towards the map centre (new leaks and the pickup appear anywhere)."""
        centre = self.idx((view.width // 2, view.height // 2))
        if self.me != centre:
            action = self._go({centre: 0.0})
            if action is not None:
                return action
        return Action.detect()
