"""Pipe network invariants: connected, reciprocal, open ends only at the map edge."""

import random
from collections import deque

import pytest

from leakbots.model import Dir
from leakbots.pipes import generate_pipe_masks, pick_leaks

SEEDS = range(60)
SIZES = [(25, 25), (10, 10), (40, 20), (5, 5)]


def pipe_cells(masks):
    return {(x, y) for y, row in enumerate(masks) for x, m in enumerate(row) if m}


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("seed", SEEDS)
def test_links_are_reciprocal_or_lead_off_map(size, seed):
    w, h = size
    masks = generate_pipe_masks(w, h, random.Random(seed))
    for x, y in pipe_cells(masks):
        for d in Dir:
            if masks[y][x] & d.bit:
                nx, ny = d.step((x, y))
                if 0 <= nx < w and 0 <= ny < h:
                    assert masks[ny][nx] & d.opposite.bit, f"one-way link at {(x, y)} {d}"
                # else: off-map link, only possible on a border cell by construction


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize("seed", SEEDS)
def test_network_is_one_component_without_interior_dead_ends(size, seed):
    w, h = size
    masks = generate_pipe_masks(w, h, random.Random(seed))
    cells = pipe_cells(masks)
    # Every pipe cell has at least two connections (counting off-map links)
    assert all(bin(masks[y][x]).count("1") >= 2 for x, y in cells)
    # BFS along links reaches every pipe cell
    start = next(iter(cells))
    seen, queue = {start}, deque([start])
    while queue:
        x, y = queue.popleft()
        for d in Dir:
            n = d.step((x, y))
            if masks[y][x] & d.bit and n in cells and n not in seen:
                seen.add(n)
                queue.append(n)
    assert seen == cells
    # At least one pipe leads off the map
    assert any(
        masks[y][x] & d.bit and not (0 <= d.step((x, y))[0] < w and 0 <= d.step((x, y))[1] < h)
        for x, y in cells
        for d in Dir
    )


@pytest.mark.parametrize("seed", SEEDS)
def test_coverage_near_target(seed):
    masks = generate_pipe_masks(25, 25, random.Random(seed), coverage=0.28)
    frac = len(pipe_cells(masks)) / 625
    assert 0.25 <= frac <= 0.45


def test_same_seed_same_network():
    assert generate_pipe_masks(25, 25, random.Random(3)) == generate_pipe_masks(25, 25, random.Random(3))
    assert generate_pipe_masks(25, 25, random.Random(3)) != generate_pipe_masks(25, 25, random.Random(4))


def test_leaks_are_distinct_pipe_cells():
    masks = generate_pipe_masks(25, 25, random.Random(0))
    leaks = pick_leaks(masks, 5, random.Random(0))
    assert len(set(leaks)) == 5
    assert all(masks[y][x] for x, y in leaks)
