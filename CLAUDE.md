# LeakBots Arena — project notes for Claude

Turn-based programming game: competitors write bots (`bots/*.py`) that detect, dig up and fix
leaking water pipes on a grid. Python 3.10+, pygame-ce for the viewer, pytest for tests.

## Commands

- Setup: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
- Tests: `.venv/bin/python -m pytest -q` (must stay green; ~4 s)
- GUI: `.venv/bin/python main.py [--bots random_bot random_bot] [--seed N]`
- Benchmark: `.venv/bin/python simulate.py --bots random_bot --games 100 --seed 1`
- Headless render (screenshots, CI): set `SDL_VIDEODRIVER=dummy` before importing pygame.

## Architecture rules

- `leakbots/` (except `leakbots/gui/`) must never import pygame. The engine runs headless so
  simulations stay fast; check with `grep -rn pygame leakbots --include='*.py' | grep -v gui/`.
- Bots only receive a `BotView` (`leakbots/bot_api.py`). Never pass `Game`, `Cell` or
  `BotState` objects to bot code. New information for bots = new public `BotView` member.
- Hidden truth (`Cell.dug_by`, buried pipes, other bots' `detected`) must not be reachable
  through public `BotView`/`CellInfo` members. `tests/test_rules.py` asserts this.
- All rule numbers live in `GameConfig` (`leakbots/config.py`). No magic numbers in `game.py`.
- Every new action: add to `ActionType`, an `Action.<name>()` constructor, a branch in
  `Game.apply_action`, an effect in `gui/render.py::BoardRenderer.on_turn`, a row in the
  README rules table, and tests for each valid/invalid case.
- Randomness: the engine uses `Game.rng` seeded from `seed`; bots get `self.rng`. Same seed
  must replay the same game (`test_same_seed_same_game`).

## Invariants covered by tests

- Pipe networks: one component, reciprocal links, no interior dead ends, off-map links only
  on border cells (`tests/test_pipes.py`, 60 seeds x 4 sizes).
- Score = 1·pipes_detected + 5·leaks_found + 15·leaks_fixed − 2·open_holes
  (`tests/test_simulation.py`). Update both tests and README when point values change.
- Turn order is strict round-robin; game ends exactly at `max_rounds`.

## Conventions

- PEP 8, line length 110, type hints, brief inline comments on non-obvious logic.
- Coordinates are `(x, y)` tuples, `y` grows downward; grids are indexed `cells[y][x]`.
- Bot specs on the CLI are `module:Class` relative to `bots/` (see `leakbots/loader.py`).
- GUI changes: render a dummy-driver screenshot and look at it before calling it done.
- Writing or reviewing a competitor bot: use the `new-bot` skill (`.claude/skills/new-bot/`).
