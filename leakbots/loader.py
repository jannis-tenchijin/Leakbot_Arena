"""Find and load bot classes from the ``bots/`` package."""

from __future__ import annotations

import importlib
import inspect
from pathlib import Path

from .bot_api import BaseBot

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BOTS_DIR = PROJECT_ROOT / "bots"
SPRITES_DIR = BOTS_DIR / "sprites"


def _bot_classes_in(module) -> list[type[BaseBot]]:
    """BaseBot subclasses defined in a module (not imported into it)."""
    return [
        obj
        for obj in vars(module).values()
        if inspect.isclass(obj) and issubclass(obj, BaseBot) and obj is not BaseBot and obj.__module__ == module.__name__
    ]


def discover_bots(
    bots_dir: Path = BOTS_DIR, package: str = "bots"
) -> tuple[list[type[BaseBot]], list[str]]:
    """Every bot class in every ``*.py`` file of ``bots/``, sorted by display name.

    Returns (bots, failed_files). A file that fails to import (e.g. a competitor's syntax
    error) is skipped and reported instead of crashing the arena.
    """
    bots: list[type[BaseBot]] = []
    failed: list[str] = []
    for path in sorted(bots_dir.glob("*.py")):
        if path.name.startswith("_"):
            continue
        try:
            module = importlib.import_module(f"{package}.{path.stem}")
        except Exception as exc:  # any error in a bot file must not take the arena down
            print(f"Could not load {path.name}: {type(exc).__name__}: {exc}")
            failed.append(path.name)
            continue
        bots.extend(_bot_classes_in(module))
    return sorted(bots, key=lambda c: (c.display_name().lower(), c.__name__)), failed


def bot_spec(bot_class: type[BaseBot]) -> str:
    """The ``module:Class`` spec of a bot class, as used by --bots and ?bots=."""
    return f"{bot_class.__module__.rsplit('.', 1)[-1]}:{bot_class.__name__}"


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
    found = _bot_classes_in(module)
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
