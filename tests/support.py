"""Shared fixtures for the test suite.

The game data is loaded once per process: every optimiser test needs it, and
parsing the armour file is most of what a short test would otherwise cost.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from functools import lru_cache
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from load_data import GameData, Skill, load_game_data  # noqa: E402


@lru_cache(maxsize=1)
def game() -> GameData:
    return load_game_data()


def weighted(weights: dict[str, tuple[float, float]]) -> list[Skill]:
    """Every skill at 0/0 except the (weight, level_weight) pairs given.

    Unknown names raise, so a typo in a test fails loudly instead of quietly
    testing an unweighted run.
    """
    skills = game().skills
    known = {s.name for s in skills}
    unknown = set(weights) - known
    if unknown:
        raise KeyError(f"not in skills_default.yaml: {sorted(unknown)}")
    return [
        replace(s, weight=weights[s.name][0], level_weight=weights[s.name][1])
        if s.name in weights
        else replace(s, weight=0.0, level_weight=0.0)
        for s in skills
    ]


def import_gui():
    """skills_gui, importable with or without Tk.

    With Tk installed (any normal Windows Python) the real module is used; the
    GUI tests never create a window, they call methods on stand-in objects.
    Without Tk - a bare Linux Python - tkinter is replaced by mocks just far
    enough for the import to succeed.
    """
    try:
        import tkinter  # noqa: F401
    except ImportError:
        tk = mock.MagicMock()
        sys.modules.update(
            {
                "tkinter": tk,
                "tkinter.filedialog": tk.filedialog,
                "tkinter.font": tk.font,
                "tkinter.messagebox": tk.messagebox,
                "tkinter.ttk": tk.ttk,
            }
        )
    import skills_gui

    return skills_gui


class Value:
    """Stands in for a tk.StringVar: get() and set() and nothing else."""

    def __init__(self, value: str = "") -> None:
        self.value = value

    def get(self) -> str:
        return self.value

    def set(self, value: str) -> None:
        self.value = value
