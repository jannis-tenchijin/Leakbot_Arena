"""Open the LeakBots Arena window.

    python main.py                                        # single player, one RandomBot
    python main.py --bots random_bot random_bot           # 1 vs 1
    python main.py --bots my_bot:MyBot --seed 7 --size 30 30
"""

from __future__ import annotations

import argparse
from dataclasses import replace

from leakbots.config import DEFAULT_CONFIG
from leakbots.loader import load_bot


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bots", nargs="+", default=["random_bot:RandomBot"], help="module:Class in bots/")
    parser.add_argument("--seed", type=int, default=None, help="map seed for the first game")
    parser.add_argument("--rounds", type=int, default=DEFAULT_CONFIG.max_rounds)
    parser.add_argument("--leaks", type=int, default=DEFAULT_CONFIG.n_leaks)
    parser.add_argument("--size", type=int, nargs=2, metavar=("W", "H"), default=None)
    parser.add_argument("--sim-games", type=int, default=100, help="games played by the SIMULATE button")
    args = parser.parse_args()

    config = replace(DEFAULT_CONFIG, max_rounds=args.rounds, n_leaks=args.leaks)
    if args.size:
        config = replace(config, width=args.size[0], height=args.size[1])
    bot_classes = [load_bot(spec) for spec in args.bots]

    from leakbots.gui.app import App  # imported late so --help works without a display

    App(bot_classes, config, args.seed, args.sim_games).run()


if __name__ == "__main__":
    main()
