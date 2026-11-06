"""Random pipe network generator.

The network is grown from an inlet on the map border by "heads" that crawl across the
grid, preferring long straight runs. A head only stops when it leaves the map (creating
an off-map link) or joins existing pipe, so the result is always:

* one connected component,
* free of interior dead ends (every pipe cell has >= 2 connections),
* reciprocal (if A links to B, B links to A); open ends exist only at the map edge.

New branches need one cell of clearance from other pipes, which keeps the network
readable instead of clumping into dense knots.
"""

from __future__ import annotations

import random
from collections.abc import Iterable
from dataclasses import dataclass

from .model import RISK_GRADES, Dir, Pos

# Shape tuning
MIN_STRAIGHT = 3  # cells a head must run straight before it may turn
P_TURN = 0.15  # chance to turn once MIN_STRAIGHT is reached
P_BRANCH = 0.08  # chance to spawn a side branch from a freshly laid cell
P_EXIT_AT_EDGE = 0.55  # chance a head reaching the border leads off-map (else it turns)

_STRAIGHT_MASKS = (Dir.UP.bit | Dir.DOWN.bit, Dir.LEFT.bit | Dir.RIGHT.bit)


@dataclass
class _Head:
    """A growing pipe end: the last laid cell and the direction it wants to grow."""

    pos: Pos
    direction: Dir
    run: int = 0  # cells laid since the last turn


class _Builder:
    """Mutable mask grid with the helpers the growth loop needs."""

    def __init__(self, width: int, height: int) -> None:
        self.w, self.h = width, height
        self.masks = [[0] * width for _ in range(height)]

    def inside(self, p: Pos) -> bool:
        return 0 <= p[0] < self.w and 0 <= p[1] < self.h

    def is_pipe(self, p: Pos) -> bool:
        return self.inside(p) and self.masks[p[1]][p[0]] != 0

    def mask(self, p: Pos) -> int:
        return self.masks[p[1]][p[0]]

    def link(self, a: Pos, d: Dir) -> None:
        """Connect cell a with its neighbour in direction d (off-map if outside)."""
        self.masks[a[1]][a[0]] |= d.bit
        b = d.step(a)
        if self.inside(b):
            self.masks[b[1]][b[0]] |= d.opposite.bit

    def clear_around(self, p: Pos, came_from: Pos) -> bool:
        """True if p is empty and none of its neighbours (except came_from) hold pipe."""
        if not self.inside(p) or self.is_pipe(p):
            return False
        return all(not self.is_pipe(d.step(p)) for d in Dir if d.step(p) != came_from)


def generate_pipe_masks(
    width: int, height: int, rng: random.Random, coverage: float = 0.28
) -> list[list[int]]:
    """Return masks[y][x] with connection bits (Dir.bit) for every cell; 0 = no pipe."""
    b = _Builder(width, height)
    target = max(2, int(width * height * coverage))
    laid = 0

    # Inlet: a random border cell linked off-map, growing inward
    side = rng.choice(list(Dir))  # the map side the main enters from
    if side in (Dir.UP, Dir.DOWN):
        start = (rng.randrange(1, width - 1), 0 if side is Dir.UP else height - 1)
    else:
        start = (0 if side is Dir.LEFT else width - 1, rng.randrange(1, height - 1))
    b.masks[start[1]][start[0]] |= side.bit  # off-map link
    laid += 1
    heads = [_Head(start, side.opposite, run=1)]

    guard = width * height * 50  # hard stop against any unforeseen infinite loop
    while guard > 0:
        guard -= 1
        if not heads:
            if laid >= target:
                break
            branch = _spawn_branch(b, rng)
            if branch is None:
                break
            heads.append(branch)

        head = heads.pop(rng.randrange(len(heads)))
        back = head.direction.opposite  # never grow back into the previous cell
        direction = head.direction
        if head.run >= MIN_STRAIGHT and rng.random() < P_TURN:
            direction = rng.choice((direction.turn_left(), direction.turn_right()))
            head.run = 0

        nxt = direction.step(head.pos)
        if not b.inside(nxt):
            # Reached the border: lead off-map, or turn to run along the edge
            turns = [
                d
                for d in (direction.turn_left(), direction.turn_right())
                if d is not back and b.inside(d.step(head.pos))
            ]
            if rng.random() < P_EXIT_AT_EDGE or not turns:
                b.link(head.pos, direction)
                continue
            direction = rng.choice(turns)
            head.run = 0
            nxt = direction.step(head.pos)

        if b.is_pipe(nxt):
            b.link(head.pos, direction)  # join existing network; head ends
            continue

        # Lay a new cell
        b.link(head.pos, direction)
        laid += 1

        # Pipe right beside or ahead of the new cell: connect to it instead of running parallel
        touching = [d for d in Dir if d is not direction.opposite and b.is_pipe(d.step(nxt))]
        if touching:
            b.link(nxt, direction if direction in touching else rng.choice(touching))
            continue

        new_head = _Head(nxt, direction, head.run + 1)
        heads.append(new_head)
        if laid < target and new_head.run >= 2 and rng.random() < P_BRANCH:
            side_dir = rng.choice((direction.turn_left(), direction.turn_right()))
            if b.clear_around(side_dir.step(nxt), came_from=nxt):
                heads.append(_Head(nxt, side_dir, 0))
                new_head.run = 0  # main line keeps straight past the junction

    return b.masks


def _spawn_branch(b: _Builder, rng: random.Random) -> _Head | None:
    """Start a new head from a straight pipe cell whose neighbour has room for a branch."""
    candidates = []
    fallback = []
    for y in range(b.h):
        for x in range(b.w):
            m = b.masks[y][x]
            if not m:
                continue
            for d in Dir:
                target = d.step((x, y))
                if b.clear_around(target, came_from=(x, y)):
                    (candidates if m in _STRAIGHT_MASKS else fallback).append(((x, y), d))
    pool = candidates or fallback
    if not pool:
        return None
    pos, d = rng.choice(pool)
    return _Head(pos, d, run=0)


def pick_leaks(masks: list[list[int]], n_leaks: int, rng: random.Random) -> list[Pos]:
    """Choose n distinct pipe cells to be broken at game start."""
    pipe_cells = [(x, y) for y, row in enumerate(masks) for x, m in enumerate(row) if m]
    return rng.sample(pipe_cells, min(n_leaks, len(pipe_cells)))


def pick_risk_grades(
    masks: list[list[int]], leaks: Iterable[Pos], rng: random.Random, decay: float
) -> dict[Pos, str]:
    """Satellite risk grade of every pipe cell: a strong hint about leaks, not proof.

    Each cell draws a level k = 0..3 with P(k) ~ decay**k. Intact pipes map k to A, B, C, D
    and leaks map it mirrored to E, D, C, B, so the rarest grade of either kind is the one
    that looks most like the other kind.
    """
    levels = range(len(RISK_GRADES) - 1)  # 4 levels per kind: A-D intact, E-B leaking
    weights = [decay**k for k in levels]
    leak_set = set(leaks)
    grades: dict[Pos, str] = {}
    for y, row in enumerate(masks):
        for x, m in enumerate(row):
            if not m:
                continue
            k = rng.choices(levels, weights)[0]
            grades[(x, y)] = RISK_GRADES[-1 - k] if (x, y) in leak_set else RISK_GRADES[k]
    return grades
