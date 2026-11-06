"""Competitor template: copy this file to bots/<your_name>_bot.py and fill in act().

Run it:    python main.py --bots <your_name>_bot:MyBot
Benchmark: python simulate.py --bots <your_name>_bot:MyBot --games 100

Moves (each targets the neighbouring cell in a Dir: UP, DOWN, LEFT, RIGHT):
    Action.move(d)     walk onto an untouched, free cell
    Action.detect(d)   privately learn if a pipe is buried there      (+1 per new pipe cell)
    Action.unearth(d)  dig the cell open; holes cannot be walked on   (+5 if it reveals a leak)
    Action.fix(d)      repair an unearthed broken pipe                (+15)
    Action.refill(d)   fill any hole again (not a still-leaking pipe)
At game end every pipe hole you dug and left open costs -2.

Useful view members (see leakbots/bot_api.py for all):
    view.me, view.width, view.height, view.round, view.rounds_left
    view.cell(pos) -> CellInfo(surface, has_pipe, pipe_state, pipe_dirs, bot)
    view.is_walkable(pos), view.neighbor(d), view.detected, view.last_result
"""

from leakbots import Action, BaseBot, BotView, Dir


class MyBot(BaseBot):
    name = "MyBot"  # shown on the scoreboard
    sprite = None  # e.g. "my_bot.png" in bots/sprites/ (or drag a PNG onto your card in the GUI)

    def on_game_start(self, view: BotView) -> None:
        # Set up your own memory here, e.g. which holes you dug
        self.my_holes = set()

    def act(self, view: BotView) -> Action:
        # TODO: replace with your strategy
        return Action.detect(Dir.UP)
