"""Advanced tab: voices per speaker, timing, extra outputs, loudness, pronunciation, performance."""

from __future__ import annotations

from tkinter import messagebox
from typing import TYPE_CHECKING, Dict, List, Optional, Sequence, Tuple

import customtkinter as ctk

from .dialog_tts.voices import VoiceSpec

from . import system
from . import theme as t
from . import widgets as w
from .catalog import Catalog
from .settings import ROLE_NAME, Speaker

if TYPE_CHECKING:
    from .app import ParleyApp


def pct(v: float) -> str:
    return f"{int(round(v)):+d} %"


def hz(v: float) -> str:
    return f"{int(round(v)):+d} Hz"


def ms(v: float) -> str:
    return f"{int(round(v))} ms"


Options = List[Tuple[str, str]]   # (label, voice name)


def voice_options(catalog: Catalog, locale: str, current: str = "") -> Options:
    opts = [(v.label, v.name) for v in catalog.voices_for(locale)]
    if current and current not in {n for _, n in opts}:
        v = catalog.get(current)
        opts.insert(0, (f"{v.label} · {v.locale}" if v else current, current))
    return opts


class VoicePicker:
    """An option menu whose first entry is "Automatic · <voice it resolves to>"."""

    def __init__(self, master, voice_var, width: int = 260):
        self.voice_var = voice_var
        self.options: Options = []
        self.auto = "Automatic"
        self.menu = w.option_menu(master, [], command=self._pick, width=width)

    def update(self, options: Options, auto_label: str) -> None:
        self.options = options
        self.auto = f"Automatic · {auto_label}" if auto_label else "Automatic"
        self.menu.configure(values=[self.auto] + [label for label, _ in options])
        current = self.voice_var.get()
        self.menu.set(next((lb for lb, n in options if n == current), current) if current else self.auto)

    def _pick(self, label: str) -> None:
        self.voice_var.set("" if label == self.auto else dict(self.options).get(label, ""))


class SpeakerRow(ctk.CTkFrame):
    def __init__(self, master, app: "ParleyApp", role: str):
        super().__init__(master, fg_color=t.FIELD, corner_radius=8)
        self.app, self.role = app, role
        self.vars = sv = app.model.speaker(role)
        self.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(self, text=role, height=26, corner_radius=13, fg_color=t.ACCENT_SOFT, text_color=t.ACCENT,
                     font=t.font(12, "bold"), width=34).grid(row=0, column=0, padx=(10, 10), pady=(10, 4), sticky="w")
        self.picker = VoicePicker(self, sv.voice)
        self.picker.menu.grid(row=0, column=1, sticky="w", pady=(10, 4))
        self.note = w.caption(self, "")
        self.note.grid(row=0, column=2, sticky="e", padx=8, pady=(10, 4))
        w.icon_button(self, "▶", lambda: app.preview_speaker(role)).grid(row=0, column=3, pady=(10, 4))
        w.icon_button(self, "↺", lambda: sv.set(Speaker())).grid(row=0, column=4, padx=(0, 6), pady=(10, 4))
        sliders = ctk.CTkFrame(self, fg_color="transparent")
        sliders.grid(row=1, column=0, columnspan=5, sticky="ew", padx=(54, 12), pady=(0, 10))
        for col, (text, var, fmt) in enumerate([("Speed", sv.rate, pct), ("Pitch", sv.pitch, hz),
                                                ("Volume", sv.volume, pct)]):
            sliders.grid_columnconfigure(col, weight=1, uniform="s")
            w.MiniSlider(sliders, text, var, -50, 50, 5, fmt).grid(row=0, column=col, sticky="ew", padx=(0, 14))

    def update(self, options: Options, used: Optional[VoiceSpec], catalog: Catalog, pinned: bool) -> None:
        auto = catalog.get(used.voice).label if used and catalog.get(used.voice) else (used.voice if used else "")
        self.picker.update(options, auto)
        if not pinned:
            note = ""
        elif self.vars.voice.get():
            note = "replaces the text's @voices"
        else:
            note = "voice from the text's @voices"
        self.note.configure(text=note)


class AdvancedTab(ctk.CTkScrollableFrame):
    def __init__(self, master, app: "ParleyApp"):
        super().__init__(master, fg_color="transparent", scrollbar_button_color=t.FIELD_HOVER)
        self.app = app
        m = app.model
        self.grid_columnconfigure(0, weight=1)
        self.rows: Dict[str, SpeakerRow] = {}
        self.extra_roles: List[str] = []
        self._shown: List[str] = []
        self._roles: List[str] = []
        self._voices: Dict[str, VoiceSpec] = {}
        self._pinned: Sequence[str] = ()

        # Voices -------------------------------------------------------------------------------
        card = self._card(0, "Voices", "Automatic uses the voice from the text's @voices block if it has one, "
                                       "otherwise a different voice per speaker in the chosen language. "
                                       "A voice you pick and the sliders always apply.")
        b = card.body
        w.label(b, "Narrator", weight="bold").grid(row=0, column=0, sticky="w")
        self.narrator_caption = w.caption(b, "")
        self.narrator_caption.grid(row=0, column=1, sticky="w", padx=(8, 0))
        self.set_mode("narration")
        nar = ctk.CTkFrame(b, fg_color=t.FIELD, corner_radius=8)
        nar.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(6, 14))
        nar.grid_columnconfigure(0, weight=1)
        self.narrator = VoicePicker(nar, m.narrator.voice)
        self.narrator.menu.grid(row=0, column=0, sticky="w", padx=10, pady=(10, 4))
        w.icon_button(nar, "▶", app.preview_narrator).grid(row=0, column=1, pady=(10, 4))
        w.icon_button(nar, "↺", lambda: m.narrator.set(Speaker())).grid(row=0, column=2, padx=(0, 6), pady=(10, 4))
        ns = ctk.CTkFrame(nar, fg_color="transparent")
        ns.grid(row=1, column=0, columnspan=3, sticky="ew", padx=(10, 12), pady=(0, 10))
        for col, (text, var, fmt) in enumerate([("Speed", m.narrator.rate, pct), ("Pitch", m.narrator.pitch, hz),
                                                ("Volume", m.narrator.volume, pct)]):
            ns.grid_columnconfigure(col, weight=1, uniform="s")
            w.MiniSlider(ns, text, var, -50, 50, 5, fmt).grid(row=0, column=col, sticky="ew", padx=(0, 14))

        head = ctk.CTkFrame(b, fg_color="transparent")
        head.grid(row=2, column=0, columnspan=3, sticky="ew")
        head.grid_columnconfigure(1, weight=1)
        w.label(head, "Dialogue speakers", weight="bold").grid(row=0, column=0, sticky="w")
        w.ghost(head, "+ Add speaker", self.add_speaker).grid(row=0, column=2, sticky="e")
        self.speaker_box = ctk.CTkFrame(b, fg_color="transparent")
        self.speaker_box.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(6, 0))
        self.speaker_box.grid_columnconfigure(0, weight=1)
        self.empty = w.caption(self.speaker_box, "No speakers yet. Write lines like [A] Hello! or insert the "
                                                 "example from the Guide; each speaker then appears here.")

        # Dialogue timing ----------------------------------------------------------------------
        b = self._card(1, "Dialogue timing", "Silence inserted between lines. [pause N] in the text always wins.").body
        w.SliderRow(b, 0, "Between speakers", m["gap_change"], 0, 2000, 50, ms)
        w.SliderRow(b, 1, "Same speaker", m["gap_same"], 0, 2000, 50, ms)
        w.SliderRow(b, 2, "Between repeats", m["gap_repeat"], 0, 3000, 50, ms)

        # Extra outputs ------------------------------------------------------------------------
        b = self._card(2, "Extra outputs", "Written next to the main MP3. Dialogues only.").body
        b.grid_columnconfigure((0, 1), weight=1, uniform="o")
        for i, (key, text) in enumerate([("srt", "SRT subtitles"), ("lrc", "LRC lyrics"),
                                         ("shadow", "Shadowing version (repeat-after-me gaps)"),
                                         ("slow", "Slow version (−20 % speed)"),
                                         ("clips", "Per-line clips"), ("wav", "WAV copies")]):
            w.switch(b, text, m[key]).grid(row=i // 2, column=i % 2, sticky="w", pady=5)
        shadow = ctk.CTkFrame(b, fg_color="transparent")
        shadow.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        shadow.grid_columnconfigure(1, weight=1)
        w.SliderRow(shadow, 0, "Shadowing gap", m["shadow_factor"], 0.5, 3.0, 0.1, lambda v: f"{v:.1f} × line")

        # Sound --------------------------------------------------------------------------------
        b = self._card(3, "Sound", "Every speaker is levelled to the same loudness, then the whole file "
                                   "to the target (never above −1 dBFS peaks).").body
        w.label(b, "Measure", width=150).grid(row=0, column=0, sticky="w")
        self.loudness = w.segmented(b, ["LUFS (EBU R128)", "dBFS"], command=self._pick_loudness)
        self.loudness.grid(row=0, column=1, sticky="w", padx=(8, 0), pady=4)
        self.loudness.set("dBFS" if m["loudness"].get() == "dbfs" else "LUFS (EBU R128)")
        w.SliderRow(b, 1, "Target", m["target"], -30, -10, 1,
                    lambda v: f"{v:.0f} {'dBFS' if m['loudness'].get() == 'dbfs' else 'LUFS'}")

        # Pronunciation & parsing --------------------------------------------------------------
        b = self._card(4, "Pronunciation & parsing").body
        w.label(b, "Lexicon file", width=150).grid(row=0, column=0, sticky="w")
        w.PathPicker(b, m["lexicon"], kind="file", title="Lexicon (word = spoken form)",
                     filetypes=(("Text", "*.txt"), ("All files", "*")), clearable=True).grid(
            row=0, column=1, columnspan=2, sticky="ew", padx=(8, 0))
        w.caption(b, "Lines like  Melange = Melahnsch. Empty: lexicon.txt next to the opened file, if any.").grid(
            row=1, column=1, columnspan=2, sticky="w", padx=(8, 0), pady=(4, 8))
        w.switch(b, "Untagged lines continue the previous speaker", m["continue_speaker"]).grid(
            row=2, column=0, columnspan=3, sticky="w")

        # Performance --------------------------------------------------------------------------
        b = self._card(5, "Performance").body
        w.SliderRow(b, 0, "Parallel requests", m["concurrency"], 1, 8, 1, lambda v: f"{v:.0f}")
        w.SliderRow(b, 1, "Narration chunk", m["chunk_size"], 500, 5000, 250, lambda v: f"{v:,.0f} chars")
        w.label(b, "Cache folder", width=150).grid(row=2, column=0, sticky="w", pady=(6, 0))
        w.PathPicker(b, m["cache_dir"], kind="dir", title="Cache folder").grid(
            row=2, column=1, columnspan=2, sticky="ew", padx=(8, 0), pady=(6, 0))
        cache = ctk.CTkFrame(b, fg_color="transparent")
        cache.grid(row=3, column=1, columnspan=2, sticky="ew", padx=(8, 0), pady=(6, 0))
        self.cache_info = w.caption(cache, "")
        self.cache_info.grid(row=0, column=0, sticky="w")
        w.ghost(cache, "Clear cache", self.clear_cache).grid(row=0, column=1, padx=(12, 0))

        # Appearance ---------------------------------------------------------------------------
        b = self._card(6, "Appearance").body
        w.label(b, "Theme", width=150).grid(row=0, column=0, sticky="w")
        w.segmented(b, ["System", "Light", "Dark"], variable=m["appearance"]).grid(row=0, column=1, sticky="w",
                                                                                   padx=(8, 0))
        w.ghost(b, "Reset all settings", app.reset_settings, text_color=t.DANGER).grid(row=0, column=2, sticky="e")

        self.refresh_cache_info()

    def _card(self, row: int, title: str, text: Optional[str] = None) -> w.Card:
        card = w.Card(self, title, text)
        card.grid(row=row, column=0, sticky="ew", pady=(0, 10), padx=(0, 6))
        return card

    # --- voices -------------------------------------------------------------------------------
    def set_mode(self, mode: str) -> None:
        if mode == "dialogue":
            self.narrator_caption.configure(text="plain text only, not used for this dialogue; "
                                                 "use the speaker rows below", text_color=t.WARNING)
        else:
            self.narrator_caption.configure(text="reads plain text (no speaker tags)", text_color=t.MUTED)

    def refresh_voices(self, catalog: Catalog, narrator_auto: str) -> None:
        locale = self.app.model["language"].get()
        self.narrator.update(voice_options(catalog, locale, self.app.model.narrator.voice.get()), narrator_auto)
        self._update_rows(catalog)

    def set_roles(self, roles: List[str], voices: Dict[str, VoiceSpec], pinned: Sequence[str],
                  catalog: Catalog) -> None:
        self._roles, self._voices, self._pinned = roles, voices, pinned
        shown = roles + [r for r in self.extra_roles if r not in roles]
        if shown != self._shown:
            for row in self.rows.values():
                row.grid_forget()
            for i, role in enumerate(shown):
                if role not in self.rows:
                    self.rows[role] = SpeakerRow(self.speaker_box, self.app, role)
                self.rows[role].grid(row=i, column=0, sticky="ew", pady=(0, 8))
            if shown:
                self.empty.grid_forget()
            else:
                self.empty.grid(row=0, column=0, sticky="w")
            self._shown = shown
        self._update_rows(catalog)

    def _update_rows(self, catalog: Catalog) -> None:
        locale = self.app.model["language"].get()
        for role in self._shown:
            row = self.rows[role]
            row.update(voice_options(catalog, locale, row.vars.voice.get()), self._voices.get(role), catalog,
                       role in self._pinned)

    def add_speaker(self) -> None:
        dialog = ctk.CTkInputDialog(title="Add speaker",
                                    text="Speaker name as used in the text, e.g. D or Anna\n(letters and digits, "
                                         "no spaces)", button_fg_color=t.ACCENT, button_hover_color=t.ACCENT_HOVER)
        name = (dialog.get_input() or "").strip().strip("[]")
        if not name:
            return
        if not ROLE_NAME.match(name):
            messagebox.showerror("Add speaker", f"“{name}” can't be a speaker name: use letters, digits or _, "
                                                "starting with a letter, and no spaces.", parent=self)
            return
        if name not in self.extra_roles:
            self.extra_roles.append(name)
        self.app.validate_now()

    # --- sound / cache ------------------------------------------------------------------------
    def _pick_loudness(self, label: str) -> None:
        mode = "dbfs" if label == "dBFS" else "lufs"
        if mode != self.app.model["loudness"].get():
            self.app.model["loudness"].set(mode)
            self.app.model["target"].set(-20.0 if mode == "dbfs" else -16.0)

    def sync_loudness(self) -> None:
        self.loudness.set("dBFS" if self.app.model["loudness"].get() == "dbfs" else "LUFS (EBU R128)")

    def refresh_cache_info(self) -> None:
        size = system.cache_size(self.app.model["cache_dir"].get())
        self.cache_info.configure(text=f"{system.human_size(size)} of synthesised lines. Re-runs only "
                                       "synthesise lines that changed.")

    def clear_cache(self) -> None:
        if messagebox.askyesno("Clear cache", "Delete all cached voice lines? The next run synthesises "
                                              "every line again.", parent=self):
            n = system.clear_cache(self.app.model["cache_dir"].get())
            self.refresh_cache_info()
            self.app.set_status(f"Cache cleared ({n} lines).", "info")
