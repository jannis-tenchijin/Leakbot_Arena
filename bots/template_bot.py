"""Competitor template: copy this file to bots/<your_name>_bot.py and fill in act().

Run it:    python main.py --bots <your_name>_bot:MyBot
Benchmark: python simulate.py --bots <your_name>_bot:MyBot --rounds 100

A move = one action by your bot. A round = one game: every bot makes 500 moves.
The square you stand on is detected for free (at the start and after every step).

Actions (directional ones aim at the neighbouring square in a Dir: UP, DOWN, LEFT, RIGHT):
    Action.move(d)     step onto an untouched, free square          (+1 if it is a new pipe square)
    Action.detect()    scan all four neighbours at once (private)   (+1 per new pipe square)
    Action.unearth(d)  dig the square open; holes cannot be walked on   (+5 if it reveals a leak)
    Action.fix(d)      repair an unearthed broken pipe              (+15)
    Action.refill(d)   fill any hole again (not a still-leaking pipe)
    Action.buy(PowerUp.UT)         15 points: detects also report leaks
    Action.buy(PowerUp.SATELLITE)  25 points: whole pipe network with A-E risk grades
    Action.chain(d)    punk only, once per round: ball and chain on an adjacent opponent
Move 200: a punk pickup appears (view.punk_pos); step on it for +10 and a mohawk.
Chained bots need two moves per step (view.chained_moves(), view.dragging).
At the end of the round every pipe hole you dug and left open costs -2.

Useful view members (see leakbots/bot_api.py for all):
    view.me, view.width, view.height, view.move, view.moves_left
    view.cell(pos) -> CellInfo(surface, has_pipe, pipe_state, pipe_dirs, bot, revealed)
    view.is_walkable(pos), view.neighbor(d), view.detected, view.detected_leaks
    view.satellite, view.risk(pos), view.upgrades, view.score, view.last_result
"""

from leakbots import Action, BaseBot, BotView, PowerUp  # noqa: F401  (PowerUp for Action.buy)


class MyBot(BaseBot):
    name = "MyBot"  # shown on the scoreboard
    sprite = None  # e.g. "my_bot.png" in bots/sprites/ (or drag a PNG onto your card in the GUI)

    def on_game_start(self, view: BotView) -> None:
        # Set up your own memory here, e.g. which holes you dug
        self.my_holes = set()

    def act(self, view: BotView) -> Action:
        # TODO: replace with your strategy
        return Action.detect()
