"""GovernmentBot: an example competitor that works like a careful public water utility.

Strategy:
    Phase 1  Find a pipe, then follow the pipe network with steps and detects. Every newly
             discovered pipe square pays +1, until the bot can afford the ultrasonic test.
    Buy      As soon as ``view.score >= view.cost(PowerUp.UT)``: buy UT (15 points).
    Phase 2  Keep scanning along the pipes. With UT every scan also reports leaks; a reported
             leak is dug up (+5), fixed (+15) and refilled straight away.
    Always   Fix broken pipes other bots left open (+15) and never leave an own hole open.

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
from collections.abc import Iterator

from leakbots import Action, ActionType, BaseBot, BotView, Dir, PipeState, Pos, PowerUp, Surface

# Chance that an unscanned square holds pipe, given what the bot already knows
BASE_CHANCE = 0.2  # before the first pipe is found (roughly 28 % of all squares are pipe)
STRAIGHT_CHANCE = 0.8  # straight ahead of a pipe square with one known link: pipes run straight
BRANCH_CHANCE = 0.1  # beside a pipe square whose two links are known: only a side branch fits

# With UT: what scanning a pipe square for a leak is worth (5 leaks hide in ~180 pipe squares)
LEAK_CHECK_VALUE = 0.5

DIG_MOVES = 3  # unearth + fix + refill: never start a dig that cannot be refilled in time


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

        # 1. Work on a hole or reported leak right next to the bot
        action = self._work_next_to_me(view, jobs)
        if action is not None:
            return action

        # 2. Phase switch: buy the ultrasonic test as soon as it is affordable
        if not view.has(PowerUp.UT) and view.score >= view.cost(PowerUp.UT):
            return Action.buy(PowerUp.UT)

        # 3. Walk to the nearest job, otherwise 4. scan the most promising squares
        return self._go_to_work(view, jobs) or self._explore(view)

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
            elif pos in self.leaks and view.moves_left >= DIG_MOVES and info.bot in (None, view.bot_id):
                jobs[pos] = "dig"  # if the bot stands on it, _go_to_work steps off first
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

    def _go_to_work(self, view: BotView, jobs: dict[Pos, str]) -> Action | None:
        """Step towards the nearest square next to a job (if the job still fits in the round)."""
        if not jobs:
            return None
        for pos, dist, first in self._reachable(view):
            if dist == 0:
                continue  # the bot's own square: jobs next to it were handled by _work_next_to_me
            for _, nxt in self.around[pos]:
                kind = jobs.get(nxt)
                needed = DIG_MOVES if kind == "dig" else 1
                if kind is not None and dist + needed <= view.moves_left:
                    return Action.move(first)
        return None

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
            if dist and top / dist <= best_rate:
                break  # nothing farther away can beat the best plan
            own = values.get(pos, 0.0) if dist else 0.0  # stepping onto pos auto-detects it
            if dist and own / dist > best_rate:
                best_rate, best = own / dist, Action.move(first)
            rate = (own + detect_gain.get(pos, 0.0)) / (dist + 1)  # walk to pos, then detect
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
    def _reachable(self, view: BotView) -> Iterator[tuple[Pos, int, Dir | None]]:
        """Breadth-first search over walkable squares: yield (square, distance, first step)."""
        start = view.me
        yield start, 0, None
        seen = {start}
        queue = deque([(start, 0, None)])
        while queue:
            pos, dist, first = queue.popleft()
            for d, nxt in self.around[pos]:
                if nxt not in seen and view.is_walkable(nxt):
                    seen.add(nxt)
                    step = first or d
                    yield nxt, dist + 1, step
                    queue.append((nxt, dist + 1, step))
