# LeakBots Arena

Program an autonomous bot that hunts for buried water leaks. Bots share a grid, take turns,
detect and dig up pipes, fix leaks — and refill their holes before the final whistle.

**▶ Play in the browser: [jannis-tenchijin.github.io/Leakbot_Arena](https://jannis-tenchijin.github.io/Leakbot_Arena/)**
— nothing to install. 1 vs 1: [`?bots=random_bot,random_bot`](https://jannis-tenchijin.github.io/Leakbot_Arena/?bots=random_bot,random_bot)

![LeakBots Arena](docs/screenshot.png)

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py                                   # single player, one RandomBot
python main.py --bots random_bot random_bot      # 1 vs 1
python simulate.py --rounds 100                  # headless benchmark (100 rounds)
pytest                                           # run the test suite
```

Requires Python 3.10+ and `pygame-ce` (installed by `requirements.txt`).

## Rules

**Terms.** A *move* is one action by one bot. A *round* is one whole game: every bot makes
500 moves (configurable), taking turns in a fixed order. In single player your bot makes every
move. The simulation plays 100 rounds.

**Arena.** A 25x25 grid (configurable). Each square holds at most one bot or game object.
Bots can never leave the grid.

**Pipes.** A random, continuous pipe network is buried under the grid at the start of every
round. Pipes never dead-end inside the map; they only end where they lead off the map edge.
Five pipe squares are broken (leaking). Bots cannot see buried pipes.

**Actions.** One action per move. Directional actions aim at the neighbouring square
(`UP`, `DOWN`, `LEFT`, `RIGHT`), never at the bot's own square.

| Action          | Valid when…                                   | Effect                                                         |
|-----------------|-----------------------------------------------|----------------------------------------------------------------|
| `move(d)`       | target is inside, untouched and free          | the bot steps there (and the new square is auto-detected)      |
| `detect()`      | always                                        | scans all four neighbouring squares at once (private result)   |
| `unearth(d)`    | target is untouched and free                  | the square becomes a hole; holes cannot be walked on           |
| `fix(d)`        | target is an unearthed **broken** pipe        | the pipe becomes fixed                                         |
| `refill(d)`     | target is any hole, except a leaking pipe     | the square becomes untouched (walkable) again                  |
| `buy(power_up)` | you have enough points, not bought yet        | pays the price; the power-up works for the rest of the round   |

**Auto-detect.** The square a bot stands on is detected for free: its spawn square at the start
of the round and every square it steps onto. It scores like `detect`.

An invalid action (blocked, outside the grid, bad target, not enough points, or the bot
crashed) wastes the move and counts as an *invalid move*.

**Power-ups.** Bought with points via `Action.buy(...)`; buying uses the move, needs enough
points, and each power-up can be bought once per round. Purchases are public.

| Power-up                      | Price | Effect |
|-------------------------------|------:|--------|
| `PowerUp.UT` (ultrasonic test) | 15 | every detect (and auto-detect) from now on also reports whether the scanned squares leak. Old results are not updated, but scanning a square again is. Seeing a leak this way pays nothing: unearth (+5) and fix (+15) it to score. |
| `PowerUp.SATELLITE` (Tenchijin satellite) | 25 | shows the whole pipe network with a risk grade **A–E** per pipe square (E = most risky). No detect points for the squares it shows. Intact pipes are graded A–D (A most likely, D about 1 in 50); leaks are graded E–B (E most likely, B about 1 in 50) — a strong hint, not proof. |

**What bots see.** Everywhere on the map, at any time: which squares are untouched, dug up or
filled back in. A dug-up square shows its pipe, which way it runs, and whether it is OK, leaking
or fixed — to every bot, near or far. Once a square has been dug up its pipe stays public, even
after a refill (`CellInfo.revealed`, `CellInfo.refilled`). Detect results are private to the bot
that detected them. Bots are **not** told who dug a hole — track your own digs.

**Scoring.**

| Event                                                         | Points |
|---------------------------------------------------------------|-------:|
| Discover a pipe square for the first time by detect or auto-detect (once per square per bot, never for a square that was ever dug up or shown by your satellite) | +1 |
| Find a leak (unearth a broken pipe, once per leak)            |     +5 |
| Fix a leak                                                    |    +15 |
| End of round: each pipe hole you dug that is still open       |     −2 |
| Buy a power-up                                                | −price |

Ranking: highest score, then fewer invalid moves, then whoever reached their score earliest.

## Controls

| Button / key          | Does                                                             |
|-----------------------|------------------------------------------------------------------|
| PLAY / `Space`        | play or pause live turns                                         |
| STEP / `S`            | advance a single turn                                            |
| RESTART / `R`         | replay the same map (same seed)                                  |
| NEW MAP / `N`         | roll a new random map                                            |
| SIMULATE 100 ROUNDS / `M` | play 100 headless rounds with the current bots, show statistics |
| PIPES / `P`           | game-master toggle: buried pipes hidden or faintly visible       |
| MARKS / `D`           | show each bot's private detect results as dots                   |
| RULES / `H`           | open the rulebook: every action, its limits and the scoring      |
| EN \| 日本語 / `L`      | switch all text between English and Japanese (top right)         |
| Speed slider / `↑ ↓`  | 1 – 500 moves per second, far right = MAX                        |

**Custom sprite:** drag a `.png` onto a bot's scoreboard card. It is saved as
`bots/sprites/<ClassName>.png` and used from then on.

## Write a bot

1. Copy `bots/template_bot.py` to `bots/<your_name>_bot.py` and rename the class.
2. Implement `act(self, view) -> Action` (called once per move).
3. Run it: `python main.py --bots <your_name>_bot:YourBot`
4. Benchmark it: `python simulate.py --bots <your_name>_bot:YourBot --rounds 200 --seed 1`

```python
from leakbots import Action, BaseBot, BotView, Dir

class ScoutBot(BaseBot):
    name = "ScoutBot"

    def act(self, view: BotView) -> Action:
        around = [view.neighbor(d) for d in Dir if view.in_bounds(view.neighbor(d))]
        if any(pos not in view.detected for pos in around):
            return Action.detect()                       # scan all four neighbours at once
        return Action.move(self.rng.choice(list(Dir)))   # the new square is auto-detected
```

Key parts of the `view` (full list in [leakbots/bot_api.py](leakbots/bot_api.py)):

| Member                          | Meaning                                                        |
|---------------------------------|----------------------------------------------------------------|
| `view.me`                       | your position `(x, y)`; `y` grows downward                     |
| `view.width`, `view.height`     | grid size                                                      |
| `view.move`, `view.moves_left`  | your move number (from 0) and moves remaining this round       |
| `view.cell(pos)`                | `CellInfo(surface, has_pipe, pipe_state, pipe_dirs, bot, revealed)`, `.refilled` |
| `view.is_walkable(pos)`         | inside, untouched and free                                     |
| `view.neighbor(d, pos=None)`    | the position next to you (or `pos`) in direction `d`           |
| `view.detected`                 | your private detect memory `{pos: had_pipe}`                   |
| `view.detected_leaks`           | with UT: `{pos: leaking}` for squares scanned after buying     |
| `view.satellite`, `view.risk(pos)` | with the satellite: `{pos: SatelliteReading(pipe_dirs, risk)}` / grade `"A"`–`"E"` |
| `view.upgrades`, `view.has(p)`, `view.cost(p)` | your power-ups, ownership check, price      |
| `view.last_result`              | outcome of your previous move (`success`, `message`, `points`, `scan`, `scan_leaks`) |
| `view.score`, `view.scores`     | your score / everyone's score                                  |
| `self.rng`                      | seeded `random.Random` — use it so rounds are reproducible     |

Fair play: only use the public members of `view` (nothing starting with `_`).

## Browser version (GitHub Pages)

The same Python code runs in the browser via [pygbag](https://pypi.org/project/pygbag/)
(pygame compiled to WebAssembly). Every push to `main` runs the tests, builds the web version
and publishes it to GitHub Pages (`.github/workflows/pages.yml`).

- Pick bots, map and language with URL parameters: `?bots=random_bot,my_bot:MyBot&seed=42&lang=ja`.
  Only bots committed to `bots/` are available online.
- Sprites: commit `bots/sprites/<ClassName>.png` (drag-and-drop works on the desktop only).
- Build and test locally:
  ```bash
  pip install -r requirements-web.txt
  python tools/build_web.py --serve       # then open http://localhost:8000
  ```
  The first load downloads the Python runtime (a few MB) from pygbag's CDN.

## Project layout

```
main.py              GUI entry point (desktop and browser)
simulate.py          headless benchmark CLI
tools/build_web.py   browser build (pygbag) used by the Pages workflow
tools/subset_font.py shrinks the Japanese font to the glyphs the UI uses
leakbots/            engine (pure Python, no pygame) + gui/ (pygame viewer)
  config.py          GameConfig: grid size, leaks, moves per round, points, power-up prices
  pipes.py           random pipe network generator
  game.py            rules, turn order, scoring
  bot_api.py         BaseBot, BotView, CellInfo, ActionResult
  simulation.py      run many rounds, aggregate statistics
  gui/i18n.py        English/Japanese UI text and the rulebook
  gui/fonts/         M PLUS Rounded 1c subset (SIL OFL 1.1, see OFL.txt)
bots/                competitor bots + sprites/
tests/               pytest suite (rules, pipes, simulation, headless GUI)
```

## Credits

Japanese text uses [M PLUS Rounded 1c](https://fonts.google.com/specimen/M+PLUS+Rounded+1c)
by Coji Morishita / M+ Fonts Project, licensed under the SIL Open Font License 1.1
([leakbots/gui/fonts/OFL.txt](leakbots/gui/fonts/OFL.txt)), subset to the characters the game uses.
