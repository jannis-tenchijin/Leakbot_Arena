"""Baseline opponent: picks a uniformly random action every move."""

from leakbots import BUYABLE, Action, ActionType, BaseBot, BotView, Dir


class RandomBot(BaseBot):
    name = "RandomBot"

    def act(self, view: BotView) -> Action:
        # self.rng is seeded by the engine, so simulations are reproducible
        kind = self.rng.choice(list(ActionType))
        if kind is ActionType.BUY:
            return Action.buy(self.rng.choice(BUYABLE))
        return Action(kind, self.rng.choice(list(Dir)))
