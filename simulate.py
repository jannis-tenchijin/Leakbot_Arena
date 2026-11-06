"""Headless benchmark: play many rounds and print per-bot statistics.

A round is one game in which every bot makes 500 moves (``--moves``).

    python simulate.py                                   # 100 rounds, one RandomBot
    python simulate.py --bots random_bot template_bot:MyBot --rounds 500 --seed 1
"""

from __future__ import annotations

import argparse
from dataclasses import replace

from leakbots.config import DEFAULT_CONFIG
from leakbots.loader import load_bot
from leakbots.simulation import run_rounds


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bots", nargs="+", default=["random_bot:RandomBot"], help="module:Class in bots/")
    parser.add_argument("--rounds", type=int, default=100, help="rounds (games) to play")
    parser.add_argument("--seed", type=int, default=None, help="first map seed (reproducible runs)")
    parser.add_argument("--moves", type=int, default=DEFAULT_CONFIG.max_moves, help="moves per bot per round")
    parser.add_argument("--leaks", type=int, default=DEFAULT_CONFIG.n_leaks)
    parser.add_argument("--size", type=int, nargs=2, metavar=("W", "H"), default=None)
    args = parser.parse_args()

    config = replace(DEFAULT_CONFIG, max_moves=args.moves, n_leaks=args.leaks)
    if args.size:
        config = replace(config, width=args.size[0], height=args.size[1])
    bot_classes = [load_bot(spec) for spec in args.bots]

    def progress(done: int, total: int) -> None:
        print(f"\r{done}/{total} rounds", end="", flush=True)

    result = run_rounds(bot_classes, args.rounds, args.seed, config, progress=progress)
    print()
    print("\n".join(result.summary_lines()))


if __name__ == "__main__":
    main()
