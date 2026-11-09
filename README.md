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
Five pipe squares are broken (leaking) at the start, and **one new leak appears at moves 200, 300
and 400** on a buried, untouched pipe (`view.leaks_total` counts them). Bots cannot see buried pipes.

**Waterworks (水道局).** A building (blue roof, water drop) on a random pipe square away from the
leaks (`view.waterworks_pos`, `CellInfo.building`). Bots can walk on it but never dig it up.
Power-ups can **only be bought while standing on it**.

**Valves.** A leak can only be fixed once the water is turned off: every in-grid square its pipe
connects to (`view.connected_neighbors(leak)`) must be unearthed, get a valve and have that valve
closed. Valves start open. Reopening a valve after the leak it isolated was fixed pays +5 (once per
valve, to whoever reopens it). Valves still closed at the end of the round cost whoever closed them
−10 each — also when a refill buried them. Valves on dug-up squares are public (`CellInfo.valve`:
`None` = no valve, `True` = open, `False` = closed).

**Actions.** One action per move. Directional actions aim at the neighbouring square
(`UP`, `DOWN`, `LEFT`, `RIGHT`), never at the bot's own square.

| Action          | Valid when…                                   | Effect                                                         |
|-----------------|-----------------------------------------------|----------------------------------------------------------------|
| `move(d)`       | target is inside, untouched and free          | the bot steps there (and the new square is auto-detected)      |
| `detect()`      | always                                        | scans the four neighbouring squares inside the grid at once (private result) |
| `unearth(d)`    | target is untouched and free                  | the square becomes a hole; holes cannot be walked on           |
| `fix(d)`        | target is an unearthed **broken** pipe and every square it connects to has a **closed valve** | the pipe becomes fixed (+40) |
| `install_valve(d)` | target is an unearthed pipe square without a valve | puts an (open) valve there                              |
| `close_valve(d)` | target is an unearthed square with an open valve | turns the water off there (you are charged −10 if it is still closed at the end) |
| `open_valve(d)` | target is an unearthed square with a closed valve | turns the water on again (+5 once if the leak it isolated was fixed) |
| `refill(d)`     | target is any hole, except a leaking pipe     | the square becomes untouched (walkable) again; a valve stays buried in it |
| `buy(power_up)` | you **stand on the waterworks**, have enough points, not bought yet | pays the price; the power-up works for the rest of the round |
| `chain(d)`      | you are a punk, haven't chained yet, an opponent stands there | that opponent drags a ball and chain (see below) |

**Auto-detect.** The square a bot stands on is detected for free: its spawn square at the start
of the round and every square it steps onto. It scores like `detect`.

An invalid action (blocked, outside the grid, bad target, not enough points, or the bot
crashed) wastes the move and counts as an *invalid move*.

**Power-ups.** Bought with points via `Action.buy(...)` while standing on the waterworks; buying
uses the move, needs enough points, and each power-up can be bought once per round. Purchases are
public.

| Power-up                      | Price | Effect |
|-------------------------------|------:|--------|
| `PowerUp.UT` (ultrasonic test) | 15 | every detect (and auto-detect) from now on also reports whether the scanned squares leak. Old results are not updated, but scanning a square again is. Seeing a leak this way pays nothing: unearth (+5) and fix (+40) it to score. |
| `PowerUp.SATELLITE` (Tenchijin satellite) | 25 | shows the whole pipe network with a risk grade **A–E** per pipe square (E = most risky). No detect points for the squares it shows. Intact pipes are graded A–D (A most likely, D about 1 in 50); leaks are graded E–B (E most likely, B about 1 in 50) — a strong hint, not proof. The image is retaken every 50 of your moves (`view.satellite_age`): leaks that appear later only show up on the next refresh. |

**Punk upgrade.** At move 200 a punk pickup appears on a random free square (public:
`view.punk_pos`, `CellInfo.item == "punk"`; it blocks digging that square). The first bot to step
onto it gets **+10 points** and a red mohawk (`PowerUp.PUNK`). In 1 vs 1 and battle royale the punk
also gets **one ball and chain per round**: `Action.chain(d)` on an adjacent opponent. For the
victim's next 100 moves every step takes two moves — the first `move(d)` only drags the ball
(`view.dragging`), the second `move(d)` in the same direction steps; any other action resets the
drag. `view.can_chain`, `view.chained_moves(bot_id)` are public. In single player the pickup just
gives the 10 points and the mohawk.

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
| Fix a leak (all its connected squares valved off first)       |    +40 |
| Reopen a valve after the leak it isolated was fixed (once per valve, anyone) |     +5 |
| End of round: each pipe hole you dug that is still open       |     −2 |
| End of round: each valve you closed that is still closed (buried ones too) |    −10 |
| Buy a power-up                                                | −price |
| Collect the punk pickup                                       |    +10 |

Ranking: highest score, then fewer invalid moves, then whoever reached their score earliest.

## Controls

**Choosing bots and mode in the window:** click **SETUP** (or press `B`). Every bot in `bots/` is
loaded automatically and listed there; pick a mode tab, use `<` `>` to choose the bot for each
seat (the same bot may sit in several seats), add or remove seats in battle royale, then
**START ROUND** (or Enter). The map stays the same; NEW MAP rolls a new one. `--bots` / `?bots=`
only set the line-up you start with. A bot file that fails to import is skipped and named in a
red message instead of crashing the arena.

| Button / key          | Does                                                             |
|-----------------------|------------------------------------------------------------------|
| PLAY / `Space`        | play or pause live turns                                         |
| STEP / `S`            | advance a single turn                                            |
| RESTART / `R`         | replay the same map (same seed)                                  |
| NEW MAP / `N`         | roll a new random map                                            |
| SETUP / `B`           | choose the mode (single, 1 vs 1, battle royale 3–8 bots, manual) and the bot for every seat, then START ROUND |
| RUN 100 ROUNDS / `M`  | play 100 headless rounds with the current bots, show statistics  |
| PIPES / `P`           | game-master toggle: buried pipes hidden or faintly visible; once a bot owns the satellite, GHOST also shows the A–E risk grades |
| MARKS / `D`           | show each bot's private detect results as dots (not for satellite owners, who know every square) |
| MANUAL                | play a single-player round yourself on the same map; click again to hand back to the bots |
| RULES / `H`           | open the rulebook: every action, its limits and the scoring      |
| EN \| 日本語 / `L`      | switch all text between English and Japanese (top right)         |
| Speed slider / `↑ ↓`  | 1 – 500 moves per second, far right = MAX                        |

**Manual mode:** you play one bot with the keyboard. Start it with the MANUAL button,
`python main.py --manual` or `?mode=manual` in the browser URL. Every key that issues an
action is exactly one move; the round has the usual 500 moves.

| Key                   | Action                                                           |
|-----------------------|------------------------------------------------------------------|
| `W` `A` `S` `D`       | move up / left / down / right                                    |
| `Q`                   | detect all four neighbouring squares                             |
| hold `E` + `W A S D`  | unearth (dig up) that side                                       |
| hold `R` + `W A S D`  | fix the leak on that side                                        |
| hold `F` + `W A S D`  | refill the hole on that side                                     |
| hold `V` + `W A S D`  | install a valve on that side                                     |
| hold `C` + `W A S D`  | close the valve on that side                                     |
| hold `O` + `W A S D`  | open the valve on that side                                      |
| `1` / `2`             | buy UT / buy the satellite (only while standing on the waterworks) |

A key help box replaces the simulation panel. PLAY, STEP, the speed slider and RUN 100 ROUNDS
are off (your key presses are the clock), and so are the hotkeys that clash with the game keys
(`Space`, `S`, `R`, `D`, `M`, `↑ ↓`). RESTART, NEW MAP / `N`, PIPES / `P`, RULES / `H` and
`L` still work. An invalid move shows a red X on the board and its reason below the panel.

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
| `view.satellite_age`            | moves since your satellite image was taken (refreshed every 50) |
| `view.waterworks_pos`           | the waterworks square: stand here to buy                       |
| `view.connected_neighbors(pos)` | squares a revealed pipe connects to: valve these off before fixing `pos` |
| `view.leaks_total`              | leaks on the map so far (grows at moves 200, 300, 400)          |
| `view.upgrades`, `view.has(p)`, `view.cost(p)` | your power-ups, ownership check, price      |
| `view.last_result`              | outcome of your previous move (`success`, `message`, `points`, `scan`, `scan_leaks`) |
| `view.score`, `view.scores`     | your score / everyone's score                                  |
| `self.rng`                      | seeded `random.Random` — use it so rounds are reproducible     |

Fair play: only use the public members of `view` (nothing starting with `_`).

## Example bots

| Bot | Strategy | Solo avg (100 rounds, seed 1) |
|-----|----------|----------------------:|
| `random_bot` | random actions — the baseline | about −6 |
| `government_bot` | follows detected pipes for points, buys UT at the waterworks, scans for leaks and repairs them with valves; grabs a nearby punk pickup and chases the leader to chain it | about 419 |
| `tenchijin_bot` | earns 25 points, buys the satellite, then digs D/E squares by risk vs. walking distance and repairs leaks with valves; waits near the undug B+ squares once every leak is found; punk chase | about 449 |
| `boss_bot` | plays to win: **locks the waterworks** (digs every square around it right after spawning, so rivals cannot buy UT or the satellite; reopens it only for its own purchase), steals prepared fixes and reopen bonuses, races for the punk, chains and traps opponents, late-game hygiene | about 504 |

BossBot wins about 90–100 % of 1 vs 1 rounds against GovernmentBot and TenchijinBot and about
97 % of Boss + Government + Tenchijin battle royales. **Counter-play:** any bot may refill any
hole, so a bot that refills a hole next to the waterworks can still shop — the example bots don't.

Try them: `python main.py --bots boss_bot government_bot tenchijin_bot`, or on the web
`?bots=boss_bot,government_bot,tenchijin_bot`. Every example bot is a single, commented file —
a good starting point for your own bot.

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
