"""Small styled building blocks so every screen uses the same look."""

from __future__ import annotations

import tkinter as tk
from tkinter import filedialog
from typing import Callable, Optional, Sequence

import customtkinter as ctk

from . import theme as t


# --- factories ----------------------------------------------------------------------------------

def label(master, text: str = "", size: int = 13, weight: str = "normal", color=t.TEXT, **kw) -> ctk.CTkLabel:
    kw.setdefault("anchor", "w")
    kw.setdefault("justify", "left")
    return ctk.CTkLabel(master, text=text, font=t.font(size, weight), text_color=color, **kw)


def caption(master, text: str = "", **kw) -> ctk.CTkLabel:
    return label(master, text, size=12, color=t.MUTED, **kw)


def primary(master, text: str, command: Callable, **kw) -> ctk.CTkButton:
    kw.setdefault("height", 36)
    return ctk.CTkButton(master, text=text, command=command, fg_color=t.ACCENT, hover_color=t.ACCENT_HOVER,
                         text_color="#FFFFFF", text_color_disabled=("#C7C7D1", "#6B6B80"), corner_radius=8,
                         font=t.font(13, "bold"), **kw)


def ghost(master, text: str, command: Callable, **kw) -> ctk.CTkButton:
    kw.setdefault("height", 30)
    kw.setdefault("text_color", t.TEXT)
    return ctk.CTkButton(master, text=text, command=command, fg_color="transparent", hover_color=t.FIELD_HOVER,
                         border_width=1, border_color=t.BORDER, corner_radius=8, font=t.font(12), **kw)


def icon_button(master, text: str, command: Callable, **kw) -> ctk.CTkButton:
    kw.setdefault("width", 30)
    kw.setdefault("height", 30)
    return ctk.CTkButton(master, text=text, command=command, fg_color="transparent", hover_color=t.FIELD_HOVER,
                         text_color=t.MUTED, corner_radius=8, font=t.font(14), **kw)


def entry(master, variable: Optional[tk.Variable] = None, **kw) -> ctk.CTkEntry:
    kw.setdefault("height", 32)
    return ctk.CTkEntry(master, textvariable=variable, fg_color=t.FIELD, border_color=t.BORDER, border_width=1,
                        text_color=t.TEXT, corner_radius=8, font=t.font(13), **kw)


def option_menu(master, values: Sequence[str], variable: Optional[tk.Variable] = None,
                command: Optional[Callable] = None, **kw) -> ctk.CTkOptionMenu:
    kw.setdefault("height", 32)
    return ctk.CTkOptionMenu(master, values=list(values), variable=variable, command=command,
                             fg_color=t.FIELD, button_color=t.FIELD, button_hover_color=t.FIELD_HOVER,
                             text_color=t.TEXT, dropdown_fg_color=t.SURFACE, dropdown_text_color=t.TEXT,
                             dropdown_hover_color=t.FIELD_HOVER, corner_radius=8, dynamic_resizing=False,
                             font=t.font(13), dropdown_font=t.font(13), **kw)


def segmented(master, values: Sequence[str], variable: Optional[tk.Variable] = None,
              command: Optional[Callable] = None, **kw) -> ctk.CTkSegmentedButton:
    """iOS-style control: a light pill on a grey track."""
    kw.setdefault("height", 30)
    return ctk.CTkSegmentedButton(master, values=list(values), variable=variable, command=command,
                                  fg_color=t.FIELD, selected_color=t.SURFACE, selected_hover_color=t.SURFACE,
                                  unselected_color=t.FIELD, unselected_hover_color=t.FIELD_HOVER,
                                  text_color=t.TEXT, text_color_disabled=t.MUTED, corner_radius=8,
                                  font=t.font(12), **kw)


def switch(master, text: str, variable: tk.Variable, command: Optional[Callable] = None) -> ctk.CTkSwitch:
    return ctk.CTkSwitch(master, text=text, variable=variable, command=command, onvalue=True, offvalue=False,
                         progress_color=t.ACCENT, fg_color=t.FIELD_HOVER, button_color="#FFFFFF",
                         button_hover_color="#FFFFFF", text_color=t.TEXT, font=t.font(13))


def slider(master, variable: tk.Variable, from_: float, to: float, step: float,
           command: Optional[Callable] = None, **kw) -> ctk.CTkSlider:
    return ctk.CTkSlider(master, variable=variable, from_=from_, to=to,
                         number_of_steps=max(1, round((to - from_) / step)), command=command,
                         button_color=t.ACCENT, button_hover_color=t.ACCENT_HOVER, progress_color=t.ACCENT,
                         fg_color=t.FIELD_HOVER, height=16, **kw)


# --- composites ---------------------------------------------------------------------------------

class Card(ctk.CTkFrame):
    """A rounded surface with an optional title and caption; put content into `.body`."""

    def __init__(self, master, title: Optional[str] = None, text: Optional[str] = None, **kw):
        super().__init__(master, fg_color=t.SURFACE, corner_radius=t.RADIUS, border_width=1,
                         border_color=t.BORDER, **kw)
        self.grid_columnconfigure(0, weight=1)
        row = 0
        if title:
            label(self, title, size=14, weight="bold").grid(row=row, column=0, sticky="ew", padx=16, pady=(14, 0))
            row += 1
        if text:
            caption(self, text, wraplength=620).grid(row=row, column=0, sticky="ew", padx=16, pady=(2, 0))
            row += 1
        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.grid(row=row, column=0, sticky="nsew", padx=16, pady=(10, 14))
        self.body.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(row, weight=1)


class SliderRow:
    """label | slider | value, laid out on one grid row of *master* (columns 0-2)."""

    def __init__(self, master, row: int, text: str, variable: tk.Variable, from_: float, to: float,
                 step: float, fmt: Callable[[float], str], command: Optional[Callable] = None):
        self.variable, self.fmt = variable, fmt
        self.title = label(master, text, width=150)
        self.title.grid(row=row, column=0, sticky="w", pady=4)
        self.slider = slider(master, variable, from_, to, step, command=command)
        self.slider.grid(row=row, column=1, sticky="ew", padx=(8, 12), pady=4)
        self.value = label(master, "", width=76, anchor="e", color=t.MUTED)
        self.value.grid(row=row, column=2, sticky="e", pady=4)
        variable.trace_add("write", lambda *_: self.refresh())
        self.refresh()

    def refresh(self) -> None:
        try:
            self.value.configure(text=self.fmt(self.variable.get()))
        except (tk.TclError, ValueError):
            pass

    def set_range(self, from_: float, to: float, step: float) -> None:
        self.slider.configure(from_=from_, to=to, number_of_steps=max(1, round((to - from_) / step)))
        self.slider.set(self.variable.get())


class PathPicker(ctk.CTkFrame):
    """Entry + Browse button for a folder or a file."""

    def __init__(self, master, variable: tk.StringVar, kind: str = "dir", title: str = "Choose",
                 filetypes=(("All files", "*"),), placeholder: str = "", clearable: bool = False):
        super().__init__(master, fg_color="transparent")
        self.variable, self.kind, self.title, self.filetypes = variable, kind, title, filetypes
        self.grid_columnconfigure(0, weight=1)
        entry(self, variable, placeholder_text=placeholder).grid(row=0, column=0, sticky="ew")
        col = 1
        if clearable:
            icon_button(self, "✕", lambda: variable.set("")).grid(row=0, column=col, padx=(4, 0))
            col += 1
        ghost(self, "Browse…", self.browse, width=84, height=32).grid(row=0, column=col, padx=(6, 0))

    def browse(self) -> None:
        if self.kind == "dir":
            path = filedialog.askdirectory(title=self.title, initialdir=self.variable.get() or None)
        else:
            path = filedialog.askopenfilename(title=self.title, filetypes=list(self.filetypes))
        if path:
            self.variable.set(path)


class Badge(ctk.CTkLabel):
    """A small rounded status pill."""

    KINDS = {"neutral": (t.FIELD, t.MUTED), "accent": (t.ACCENT_SOFT, t.ACCENT),
             "success": (t.FIELD, t.SUCCESS), "danger": (t.ERROR_LINE, t.DANGER)}

    def __init__(self, master, text: str = "", kind: str = "neutral"):
        super().__init__(master, text=text, corner_radius=12, height=24, font=t.font(12, "bold"))
        self.set(text, kind)

    def set(self, text: str, kind: str = "neutral") -> None:
        bg, fg = self.KINDS[kind]
        self.configure(text=f"  {text}  " if text else "", fg_color=bg if text else "transparent", text_color=fg)


class MiniSlider(ctk.CTkFrame):
    """A compact slider with its caption and value above it (for per-speaker prosody)."""

    def __init__(self, master, text: str, variable: tk.Variable, from_: float, to: float, step: float,
                 fmt: Callable[[float], str]):
        super().__init__(master, fg_color="transparent")
        self.grid_columnconfigure(0, weight=1)
        self.variable, self.text, self.fmt = variable, text, fmt
        self.caption = caption(self, "")
        self.caption.grid(row=0, column=0, sticky="w", padx=4)
        slider(self, variable, from_, to, step, width=120).grid(row=1, column=0, sticky="ew")
        variable.trace_add("write", lambda *_: self.refresh())
        self.refresh()

    def refresh(self) -> None:
        try:
            self.caption.configure(text=f"{self.text}  {self.fmt(self.variable.get())}")
        except (tk.TclError, ValueError):
            pass
