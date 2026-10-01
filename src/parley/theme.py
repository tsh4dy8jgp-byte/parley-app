"""Look and feel: one neutral palette with a single accent, an 8 px spacing grid, system fonts.

Colours are (light, dark) pairs, which customtkinter switches automatically. Plain Tk colours
(text tags in the editor) go through `pick()`.
"""

from __future__ import annotations

import sys
import tkinter.font as tkfont
from typing import Tuple

import customtkinter as ctk

Pair = Tuple[str, str]

BG: Pair = ("#F6F6F7", "#121214")          # window
SURFACE: Pair = ("#FFFFFF", "#1B1B1E")     # cards
FIELD: Pair = ("#F1F1F3", "#242428")       # inputs, editor, segmented buttons
FIELD_HOVER: Pair = ("#E7E7EA", "#2E2E33")
BORDER: Pair = ("#E4E4E7", "#2C2C31")
TEXT: Pair = ("#18181B", "#F4F4F5")
MUTED: Pair = ("#6B6B74", "#9C9CA6")
ACCENT: Pair = ("#4F46E5", "#6D66F2")      # indigo
ACCENT_HOVER: Pair = ("#4338CA", "#857FF5")
ACCENT_SOFT: Pair = ("#EEF0FF", "#26254A")
SUCCESS: Pair = ("#15803D", "#4ADE80")
DANGER: Pair = ("#DC2626", "#F87171")
WARNING: Pair = ("#B45309", "#FBBF24")
ERROR_LINE: Pair = ("#FDE8E8", "#3A1F22")
TAG: Pair = ("#4F46E5", "#A5A1FF")         # [A] speaker tags in the editor
COMMENT: Pair = ("#8A8A94", "#7A7A85")

PAD = 8
RADIUS = 10
MOD = "Command" if sys.platform == "darwin" else "Control"     # key binding modifier
MOD_LABEL = "⌘" if sys.platform == "darwin" else "Ctrl+"


def pick(pair: Pair) -> str:
    return pair[1] if ctk.get_appearance_mode() == "Dark" else pair[0]


def font(size: int = 13, weight: str = "normal") -> ctk.CTkFont:
    return ctk.CTkFont(size=size, weight=weight)


def mono(size: int = 13) -> ctk.CTkFont:
    families = set(tkfont.families())
    for name in ("SF Mono", "Menlo", "Cascadia Mono", "Consolas", "JetBrains Mono", "DejaVu Sans Mono"):
        if name in families:
            return ctk.CTkFont(family=name, size=size)
    return ctk.CTkFont(family="Courier", size=size)


def apply(appearance: str) -> None:
    ctk.set_appearance_mode(appearance.lower())
    ctk.set_default_color_theme("blue")
