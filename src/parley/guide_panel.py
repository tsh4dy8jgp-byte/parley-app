"""The collapsible "Dialogue guide" side panel."""

from __future__ import annotations

from typing import Callable

import customtkinter as ctk

from . import theme as t
from . import widgets as w
from .guide import STEPS

WRAP = 292


class GuidePanel(ctk.CTkFrame):
    def __init__(self, master, on_insert: Callable[[], None], on_close: Callable[[], None]):
        super().__init__(master, fg_color=t.SURFACE, corner_radius=t.RADIUS, border_width=1,
                         border_color=t.BORDER, width=356)
        self.grid_propagate(False)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=(18, 8), pady=(14, 4))
        head.grid_columnconfigure(0, weight=1)
        w.label(head, "Dialogue guide", size=16, weight="bold").grid(row=0, column=0, sticky="w")
        w.icon_button(head, "✕", on_close).grid(row=0, column=1, sticky="e")
        w.caption(head, "Write a conversation for speakers A, B and C, each with their own voice.",
                  wraplength=WRAP).grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 0))

        body = ctk.CTkScrollableFrame(self, fg_color="transparent", scrollbar_button_color=t.FIELD_HOVER)
        body.grid(row=1, column=0, sticky="nsew", padx=(6, 4))
        body.grid_columnconfigure(1, weight=1)
        row = 0
        for i, step in enumerate(STEPS, 1):
            row = self._step(body, row, i, step)

        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.grid(row=2, column=0, sticky="ew", padx=18, pady=(8, 16))
        foot.grid_columnconfigure(0, weight=1)
        w.primary(foot, "Insert example dialogue", on_insert).grid(row=0, column=0, sticky="ew")
        w.caption(foot, "Uses the language picked on the Standard tab when an example exists.",
                  wraplength=WRAP).grid(row=1, column=0, sticky="w", pady=(6, 0))

    def _step(self, body, row: int, number: int, step) -> int:
        badge = ctk.CTkLabel(body, text=str(number), width=24, height=24, corner_radius=12,
                             fg_color=t.ACCENT_SOFT, text_color=t.ACCENT, font=t.font(12, "bold"))
        badge.grid(row=row, column=0, sticky="nw", padx=(10, 10), pady=(14, 0))
        w.label(body, step.title, size=13, weight="bold").grid(row=row, column=1, sticky="w", pady=(15, 0))
        w.caption(body, step.body, wraplength=WRAP - 40).grid(row=row + 1, column=1, sticky="w", pady=(2, 0))
        row += 2
        if step.sample:
            box = ctk.CTkFrame(body, fg_color=t.FIELD, corner_radius=8)
            box.grid(row=row, column=1, sticky="ew", pady=(8, 0), padx=(0, 8))
            box.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(box, text=step.sample, font=t.mono(12), text_color=t.TEXT, anchor="w", justify="left",
                         wraplength=WRAP - 90).grid(row=0, column=0, sticky="w", padx=10, pady=8)
            copy = w.icon_button(box, "⧉", lambda s=step.sample: self._copy(s, copy), width=26, height=26)
            copy.grid(row=0, column=1, sticky="ne", padx=4, pady=4)
            row += 1
        return row

    def _copy(self, text: str, button: ctk.CTkButton) -> None:
        self.clipboard_clear()
        self.clipboard_append(text)
        button.configure(text="✓")
        self.after(1200, lambda: button.configure(text="⧉"))
