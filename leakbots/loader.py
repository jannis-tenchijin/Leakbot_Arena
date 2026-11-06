"""Load bot classes from the ``bots/`` package by a short spec string."""

from __future__ import annotations

import importlib
import inspect
from pathlib import Path

from .bot_api import BaseBot

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BOTS_DIR = PROJECT_ROOT / "bots"
SPRITES_DIR = BOTS_DIR / "sprites"


def load_bot(spec: str) -> type[BaseBot]:
    """Resolve ``"module:Class"`` or ``"module"`` (single bot in file) inside ``bots/``.

    Examples: ``"random_bot:RandomBot"``, ``"random_bot"``.
    """
    module_name, _, class_name = spec.partition(":")
    module = importlib.import_module(f"bots.{module_name}")
    if class_name:
        cls = getattr(module, class_name, None)
        if not (inspect.isclass(cls) and issubclass(cls, BaseBot)):
            raise ValueError(f"{spec!r}: {class_name} is not a BaseBot subclass")
        return cls
    found = [
        obj
        for obj in vars(module).values()
        if inspect.isclass(obj) and issubclass(obj, BaseBot) and obj is not BaseBot and obj.__module__ == module.__name__
    ]
    if len(found) != 1:
        names = ", ".join(c.__name__ for c in found) or "none"
        raise ValueError(f"{spec!r}: expected exactly one bot class in module, found {names}; use module:Class")
    return found[0]


def sprite_path(bot_class: type[BaseBot]) -> Path | None:
    """PNG for a bot: its ``sprite`` attribute, else ``bots/sprites/<ClassName>.png``, else None."""
    candidates = [bot_class.sprite] if bot_class.sprite else []
    candidates.append(f"{bot_class.__name__}.png")
    for name in candidates:
        path = SPRITES_DIR / name
        if path.is_file():
            return path
    return None
