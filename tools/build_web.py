"""Build the browser version (pygbag / WebAssembly) into build/leakbots-arena/build/web/.

    python tools/build_web.py           # build only (what CI does)
    python tools/build_web.py --serve   # build and serve on http://localhost:8000

Only the files the game needs are staged, so pygbag never packs .venv, tests or docs.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STAGE = ROOT / "build" / "leakbots-arena"  # folder name becomes the app/package name
WEB_OUT = STAGE / "build" / "web"
INCLUDE = ["main.py", "leakbots", "bots"]
SKIP = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store", ".gitkeep")


def stage() -> None:
    if STAGE.exists():
        shutil.rmtree(STAGE)
    STAGE.mkdir(parents=True)
    for name in INCLUDE:
        src = ROOT / name
        if src.is_dir():
            shutil.copytree(src, STAGE / name, ignore=SKIP)
        else:
            shutil.copy2(src, STAGE / name)
    make_favicon(STAGE / "favicon.png")


def make_favicon(path: Path) -> None:
    """Render the default robot as the page icon (headless)."""
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    sys.path.insert(0, str(ROOT))
    import pygame

    from leakbots.gui.render import draw_robot
    from leakbots.gui.theme import BOT_COLORS
    from leakbots.model import Dir

    pygame.init()
    icon = pygame.Surface((64, 64), pygame.SRCALPHA)
    draw_robot(icon, (32, 36), 64, BOT_COLORS[0], Dir.RIGHT)
    pygame.image.save(icon, str(path))
    pygame.quit()


def build(serve: bool) -> None:
    cmd = [
        sys.executable, "-m", "pygbag",
        "--ume_block", "0",  # no audio, so start without waiting for a click
        "--title", "LeakBots Arena",
        "--app_name", "leakbots-arena",
    ]
    if not serve:
        cmd.append("--build")
    subprocess.run([*cmd, str(STAGE)], check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--serve", action="store_true", help="also start pygbag's local test server")
    args = parser.parse_args()
    stage()
    build(args.serve)
    if not args.serve:
        print(f"\nWeb build ready: {WEB_OUT}")


if __name__ == "__main__":
    main()
