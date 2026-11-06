"""Open the LeakBots Arena window (desktop) or page (browser build via pygbag).

Desktop:
    python main.py                                        # single player, one RandomBot
    python main.py --bots random_bot random_bot           # 1 vs 1
    python main.py --bots my_bot:MyBot --seed 7 --size 30 30

Browser: the same choices come from the page URL, e.g.
    https://<user>.github.io/leakbots-arena/?bots=random_bot,random_bot&seed=7
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import replace

from leakbots.config import DEFAULT_CONFIG
from leakbots.loader import load_bot

DEFAULT_BOTS = ["random_bot:RandomBot"]
WEB = sys.platform == "emscripten"

if WEB:
    # pygbag decides which packages to load by scanning main.py's imports, so name pygame here
    import pygame  # noqa: F401


def parse_cli():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bots", nargs="+", default=DEFAULT_BOTS, help="module:Class in bots/")
    parser.add_argument("--seed", type=int, default=None, help="map seed for the first game")
    parser.add_argument("--rounds", type=int, default=DEFAULT_CONFIG.max_rounds)
    parser.add_argument("--leaks", type=int, default=DEFAULT_CONFIG.n_leaks)
    parser.add_argument("--size", type=int, nargs=2, metavar=("W", "H"), default=None)
    parser.add_argument("--sim-games", type=int, default=100, help="games played by the SIMULATE button")
    args = parser.parse_args()
    config = replace(DEFAULT_CONFIG, max_rounds=args.rounds, n_leaks=args.leaks)
    if args.size:
        config = replace(config, width=args.size[0], height=args.size[1])
    return args.bots, args.seed, config, args.sim_games


def parse_url():
    """Read ?bots=a,b&seed=N from the page URL (pygbag exposes the JS window as platform.window)."""
    from urllib.parse import parse_qs

    try:
        import platform

        query = parse_qs(str(platform.window.location.search).lstrip("?"))
    except Exception:
        query = {}
    specs = [s for s in query.get("bots", [""])[-1].split(",") if s] or DEFAULT_BOTS
    seed_text = query.get("seed", [""])[-1]
    seed = int(seed_text) if seed_text.isdigit() else None
    return specs, seed, DEFAULT_CONFIG, 100


async def main() -> None:
    specs, seed, config, sim_games = parse_url() if WEB else parse_cli()
    try:
        bot_classes = [load_bot(spec) for spec in specs]
    except (ImportError, ValueError) as exc:
        if not WEB:
            raise
        print(f"Could not load bots {specs}: {exc}; using {DEFAULT_BOTS}")  # shows in the browser console
        bot_classes = [load_bot(spec) for spec in DEFAULT_BOTS]

    from leakbots.gui.app import App  # imported late so --help works without a display

    await App(bot_classes, config, seed, sim_games).run_async()


if __name__ == "__main__":
    asyncio.run(main())
