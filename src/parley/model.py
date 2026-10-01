"""Tk variables mirroring `Settings`, so widgets bind directly and the app reads back a `Settings`."""

from __future__ import annotations

import tkinter as tk
from dataclasses import fields
from typing import Callable, Dict

from .settings import Settings, Speaker

_VAR = {bool: tk.BooleanVar, int: tk.IntVar, float: tk.DoubleVar, str: tk.StringVar}


class SpeakerVars:
    def __init__(self, master, sp: Speaker):
        self.voice = tk.StringVar(master, sp.voice)
        self.rate = tk.IntVar(master, sp.rate)
        self.pitch = tk.IntVar(master, sp.pitch)
        self.volume = tk.IntVar(master, sp.volume)

    def get(self) -> Speaker:
        return Speaker(self.voice.get(), _int(self.rate), _int(self.pitch), _int(self.volume))

    def set(self, sp: Speaker) -> None:
        self.voice.set(sp.voice)
        self.rate.set(sp.rate)
        self.pitch.set(sp.pitch)
        self.volume.set(sp.volume)

    def trace(self, callback: Callable[[], None]) -> None:
        for v in (self.voice, self.rate, self.pitch, self.volume):
            v.trace_add("write", lambda *_: callback())


class Model:
    """`model["srt"]` is the BooleanVar for Settings.srt; `model.settings()` reads them all back."""

    def __init__(self, master, settings: Settings):
        self.master = master
        self.vars: Dict[str, tk.Variable] = {}
        for f in fields(Settings):
            if f.name in ("narrator", "speakers"):
                continue
            value = getattr(settings, f.name)
            self.vars[f.name] = _VAR[type(value)](master, value=value)
        self.narrator = SpeakerVars(master, settings.narrator)
        self.speakers: Dict[str, SpeakerVars] = {r: SpeakerVars(master, sp) for r, sp in settings.speakers.items()}
        self._speaker_listeners = []

    def __getitem__(self, name: str) -> tk.Variable:
        return self.vars[name]

    def speaker(self, role: str) -> SpeakerVars:
        if role not in self.speakers:
            self.speakers[role] = sv = SpeakerVars(self.master, Speaker())
            for cb in self._speaker_listeners:
                sv.trace(cb)
        return self.speakers[role]

    def on_change(self, callback: Callable[[], None], names=None) -> None:
        """Call *callback* whenever one of the named settings (default: all, voices included) changes."""
        for name in names or self.vars:
            self.vars[name].trace_add("write", lambda *_: callback())
        if names is None:
            self.narrator.trace(callback)
            for sv in self.speakers.values():
                sv.trace(callback)
            self._speaker_listeners.append(callback)

    def settings(self) -> Settings:
        kwargs = {}
        default = Settings()
        for name, var in self.vars.items():
            try:
                value = var.get()
            except (tk.TclError, ValueError):
                value = getattr(default, name)
            if isinstance(value, float):
                value = round(value, 2)
            kwargs[name] = value
        speakers = {r: sv.get() for r, sv in self.speakers.items() if not sv.get().is_default}
        return Settings(narrator=self.narrator.get(), speakers=speakers, **kwargs)

    def load(self, settings: Settings) -> None:
        for name, var in self.vars.items():
            var.set(getattr(settings, name))
        self.narrator.set(settings.narrator)
        for role, sv in self.speakers.items():
            sv.set(settings.speakers.get(role, Speaker()))


def _int(var: tk.IntVar) -> int:
    try:
        return int(round(float(var.get())))
    except (tk.TclError, ValueError):
        return 0
