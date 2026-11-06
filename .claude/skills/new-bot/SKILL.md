---
name: new-bot
description: Create, run and benchmark a new LeakBots competitor bot in bots/. Use when asked to write, improve or evaluate a bot strategy.
---

# New LeakBots bot

1. **Copy the template**: `cp bots/template_bot.py bots/<name>_bot.py`; rename `MyBot` to a
   unique class name and set `name = "<Display Name>"`.
2. **Read the API** in `leakbots/bot_api.py` (`BotView`, `CellInfo`, `ActionResult`) and the
   rules/scoring tables in `README.md`. Only use public `view` members (no `_` attributes).
3. **Implement `act(view) -> Action`**. Keep per-game memory in `on_game_start` (e.g. the set
   of holes this bot dug, since the view does not reveal diggers). Use `self.rng` for any
   randomness so runs are reproducible.
4. **Check validity cheaply** before acting to avoid wasted turns:
   - move: `view.is_walkable(view.neighbor(d))` (the new square is auto-detected for free)
   - detect: `Action.detect()` scans all four neighbours; skip it if they are all in `view.detected`
   - buy: `view.score >= view.cost(PowerUp.UT)` and `not view.has(PowerUp.UT)`
   - unearth: target in bounds, `view.cell(t).surface is Surface.UNTOUCHED`, `cell.bot is None`
   - fix: `cell.pipe_state is PipeState.BROKEN`
   - refill: unearthed and not `PipeState.BROKEN`
   Inspect `view.last_result.message` while debugging invalid moves.
5. **Smoke test** in the GUI: `.venv/bin/python main.py --bots <name>_bot:<Class>`.
6. **Benchmark** against the baseline with a fixed seed and compare averages:
   ```bash
   .venv/bin/python simulate.py --bots <name>_bot:<Class> --rounds 200 --seed 1
   .venv/bin/python simulate.py --bots <name>_bot:<Class> random_bot --rounds 200 --seed 1
   ```
   Report avg score, win rate and invalid moves; a good bot has invalid ≈ 0 and leaves no
   open pipe holes (holes = 0) at the end of the round.
7. **Do not** edit `leakbots/` to make a bot work; engine changes are a separate task.
