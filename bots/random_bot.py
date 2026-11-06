"""Baseline opponent: picks a uniformly random action and direction every turn."""

from leakbots import Action, ActionType, BaseBot, BotView, Dir


class RandomBot(BaseBot):
    name = "RandomBot"

    def act(self, view: BotView) -> Action:
        # self.rng is seeded by the engine, so simulations are reproducible
        return Action(self.rng.choice(list(ActionType)), self.rng.choice(list(Dir)))
