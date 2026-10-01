"""Standard tab: language, the text editor with live checking, and where to save."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Callable, Dict, List, Tuple

import customtkinter as ctk

from . import theme as t
from . import widgets as w
from .catalog import Catalog
from .jobs import Check, LanguageUse, estimate_minutes
from .settings import Settings

if TYPE_CHECKING:
    from .app import ParleyApp

PLACEHOLDER = """{first}

Plain text            → read aloud by one narrator.
Lines like [A] Hello! → a dialogue, one voice per speaker.

New to dialogues? Open the Guide (F1) or insert the example."""

_TAG = re.compile(r"^\s*\[[^\]]*\]")
HIGHLIGHT_LIMIT = 200_000      # characters; above this only problem lines are marked


class StandardTab(ctk.CTkFrame):
    def __init__(self, master, app: "ParleyApp"):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._labels: List[Tuple[str, str]] = []

        # Language & narrator ------------------------------------------------------------------
        top = w.Card(self)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        b = top.body
        b.grid_columnconfigure(1, weight=1)
        w.label(b, "Language", weight="bold").grid(row=0, column=0, sticky="w", padx=(0, 12))
        self.language = ctk.CTkComboBox(
            b, values=[], width=320, height=32, command=self._pick_language, fg_color=t.FIELD,
            border_color=t.BORDER, border_width=1, button_color=t.FIELD, button_hover_color=t.FIELD_HOVER,
            text_color=t.TEXT, dropdown_fg_color=t.SURFACE, dropdown_text_color=t.TEXT,
            dropdown_hover_color=t.FIELD_HOVER, corner_radius=8, font=t.font(13), dropdown_font=t.font(13),
            text_color_disabled=t.MUTED)
        self.language.grid(row=0, column=1, sticky="w")
        self._fixed_label = ""          # shown instead of the picker when the text decides the voices
        self._suggested = ""
        self.language.bind("<KeyRelease>", self._filter_languages)
        self.language.bind("<Return>", self._commit_language)
        self.language.bind("<FocusOut>", lambda _e: self.show_language())
        self.narrator_label = w.label(b, "Narrator", weight="bold")
        self.narrator_label.grid(row=0, column=2, sticky="e", padx=(16, 12))
        self.gender = w.segmented(b, ["Auto", "Female", "Male"], variable=app.model["gender"])
        self.gender.grid(row=0, column=3, sticky="e")
        self.language_row = ctk.CTkFrame(b, fg_color="transparent")
        self.language_row.grid_columnconfigure(0, weight=1)
        self.language_note = w.caption(self.language_row, "", wraplength=760)
        self.language_note.grid(row=0, column=0, sticky="w")
        self.language_fix = w.ghost(self.language_row, "", self._use_suggested, height=26)

        # Editor -------------------------------------------------------------------------------
        card = w.Card(self)
        card.grid(row=1, column=0, sticky="nsew", pady=(0, 10))
        e = card.body
        e.grid_columnconfigure(0, weight=1)
        e.grid_rowconfigure(1, weight=1)
        bar = ctk.CTkFrame(e, fg_color="transparent")
        bar.grid(row=0, column=0, columnspan=4, sticky="ew", pady=(0, 8))
        bar.grid_columnconfigure(0, weight=1)
        w.label(bar, "Text", size=14, weight="bold").grid(row=0, column=0, sticky="w")
        for col, (text, cmd) in enumerate([("Open…", app.open_file), ("Save…", app.save_file),
                                           ("Insert example", app.insert_example), ("Clear", app.clear_text)], 1):
            w.ghost(bar, text, cmd).grid(row=0, column=col, padx=(6, 0))

        self.editor = ctk.CTkTextbox(e, fg_color=t.FIELD, text_color=t.TEXT, corner_radius=8, border_width=0,
                                     font=t.mono(13), wrap="word", undo=True, maxundo=-1, padx=12, pady=10,
                                     spacing1=2, spacing3=2, scrollbar_button_color=t.FIELD_HOVER)
        self.editor.grid(row=1, column=0, columnspan=4, sticky="nsew")
        self.placeholder = ctk.CTkLabel(self.editor, text="", font=t.mono(13), text_color=t.MUTED,
                                        anchor="nw", justify="left", fg_color="transparent")
        self.placeholder.bind("<Button-1>", lambda _e: self.editor.focus_set())
        self.set_drop_enabled(False)
        self.drop_hint = ctk.CTkLabel(self.editor, text="  Drop .txt or .md files to open  ", height=44,
                                      corner_radius=10, fg_color=t.ACCENT_SOFT, text_color=t.ACCENT,
                                      font=t.font(15, "bold"))
        self.editor.bind("<<Modified>>", self._modified)

        self.status = w.label(e, "", size=12, color=t.MUTED, wraplength=900)
        self.status.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        self.status.bind("<Button-1>", lambda _e: self._goto_first_problem())
        self._first_problem = 0

        # Output -------------------------------------------------------------------------------
        out = w.Card(self)
        out.grid(row=2, column=0, sticky="ew")
        o = out.body
        o.grid_columnconfigure(1, weight=3)
        o.grid_columnconfigure(3, weight=1)
        w.label(o, "Save to", weight="bold").grid(row=0, column=0, sticky="w", padx=(0, 12))
        w.PathPicker(o, app.model["out_dir"], kind="dir", title="Output folder").grid(row=0, column=1, sticky="ew")
        w.label(o, "File name", weight="bold").grid(row=0, column=2, sticky="w", padx=(16, 12))
        w.entry(o, app.model["out_name"], width=180).grid(row=0, column=3, sticky="ew")
        w.caption(o, ".mp3").grid(row=0, column=4, sticky="w", padx=(6, 0))

        self.configure_tags()

    # --- text ---------------------------------------------------------------------------------
    def text(self) -> str:
        return self.editor.get("1.0", "end-1c")

    def set_text(self, text: str) -> None:
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", text)
        self.editor.edit_separator()
        self.editor.mark_set("insert", "1.0")
        self.editor.see("1.0")
        self._modified()

    def _modified(self, _event=None) -> None:
        self.editor.edit_modified(False)
        if self.text().strip():
            self.placeholder.place_forget()
        else:
            self.placeholder.place(x=14, y=12)
        self.app.schedule_validate()

    def set_drop_enabled(self, enabled: bool) -> None:
        first = ("Paste or type your text here, or drop .txt / .md files." if enabled
                 else "Paste or type your text here, or use Open… (.txt / .md).")
        self.placeholder.configure(text=PLACEHOLDER.format(first=first))

    def show_drop_hint(self, show: bool) -> None:
        if show:
            self.editor.configure(border_width=2, border_color=t.ACCENT)
            self.drop_hint.place(relx=0.5, rely=0.5, anchor="center")
            self.drop_hint.lift()
        else:
            self.editor.configure(border_width=0)
            self.drop_hint.place_forget()

    def goto_line(self, n: int) -> None:
        if n > 0:
            self.editor.mark_set("insert", f"{n}.0")
            self.editor.see(f"{n}.0")
            self.editor.focus_set()

    def _goto_first_problem(self) -> None:
        self.goto_line(self._first_problem)

    # --- language -----------------------------------------------------------------------------
    def set_languages(self, catalog: Catalog) -> None:
        self._labels = catalog.languages()
        self.language.configure(values=[label for label, _ in self._labels])
        self.show_language()

    def show_language(self) -> None:
        self.language.configure(state="normal", values=[lb for lb, _ in self._labels])
        self.language.set(self._fixed_label or self._label(self.app.model["language"].get()))
        if self._fixed_label:
            self.language.configure(state="disabled")

    def _label(self, locale: str) -> str:
        return next((lb for lb, loc in self._labels if loc == locale), locale)

    def show_language_use(self, use: LanguageUse, mode: str) -> None:
        """Say when the language picker does not decide the voices (the text or Advanced does)."""
        self.gender.configure(state="disabled" if mode == "dialogue" or use.state == "fixed" else "normal")
        self._fixed_label, self._suggested = "", ""
        note, color, fix = "", t.MUTED, ""
        if use.state == "fixed":
            self._fixed_label = f"{'Detected from text' if use.source == 'text' else 'Set in Advanced'}" \
                                f" · {use.language}"
            if mode == "narration":
                note = "The narrator voice is picked in Advanced → Voices, so this setting isn't used."
            elif use.source == "text":
                note = "Every speaker's voice is set in the text (@voices), so this setting isn't used."
            else:
                note = "Every speaker's voice is picked in Advanced → Voices, so this setting isn't used."
        elif use.state == "mismatch":
            who = ", ".join(use.auto_roles[:4]) + (" …" if len(use.auto_roles) > 4 else "")
            whose = "The text's voices are" if use.source == "text" else "The voices picked in Advanced are"
            gets = "gets" if len(use.auto_roles) == 1 else "get"
            current = self._label(self.app.model["language"].get())
            note = f"{whose} {use.language}, but {who} {gets} automatic {current} voices."
            color, fix, self._suggested = t.WARNING, f"Use {self._label(use.suggested)}", use.suggested
        if note:
            self.language_note.configure(text=note, text_color=color)
            self.language_row.grid(row=1, column=1, columnspan=3, sticky="ew", pady=(8, 0))
        else:
            self.language_row.grid_remove()
        if fix:
            self.language_fix.configure(text=fix)
            self.language_fix.grid(row=0, column=1, sticky="e", padx=(12, 0))
        else:
            self.language_fix.grid_remove()
        self.show_language()

    def _use_suggested(self) -> None:
        if self._suggested:
            self.app.model["language"].set(self._suggested)
            self.app.validate_now()

    def _filter_languages(self, event=None) -> None:
        if event is not None and event.keysym in ("Return", "Escape", "Tab"):
            return
        typed = self.language.get().strip().casefold()
        hits = [lb for lb, loc in self._labels if typed in lb.casefold() or typed == loc.casefold()]
        self.language.configure(values=hits or [lb for lb, _ in self._labels])

    def _commit_language(self, _event=None) -> str:
        typed = self.language.get().strip().casefold()
        exact = [loc for lb, loc in self._labels if typed in (lb.casefold(), loc.casefold())]
        hits = exact or [loc for lb, loc in self._labels if typed and typed in lb.casefold()]
        if hits:
            self.app.model["language"].set(hits[0])
        self.show_language()
        self.editor.focus_set()
        return "break"

    def _pick_language(self, label: str) -> None:
        locale = dict(self._labels).get(label)
        if locale:
            self.app.model["language"].set(locale)
        self.show_language()

    # --- live check ---------------------------------------------------------------------------
    def show_check(self, check: Check, catalog: Catalog, settings: Settings, narrator: str) -> None:
        d = check.detection
        self._first_problem = 0
        warn = f"   ·   ⚠ {check.warnings[0].split(': ', 1)[-1]}" if check.warnings else ""
        if d.mode == "empty":
            self._status("", t.MUTED)
        elif check.problems:
            n = len(check.problems)
            line, msg = check.problems[0]
            self._first_problem = line
            where = f"line {line}: " if line else ""
            more = f"  (+{n - 1} more)" if n > 1 else ""
            self._status(f"✕  {n} problem{'s' * (n > 1)} · {where}{msg}{more}", t.DANGER, link=bool(line))
        elif d.mode == "narration":
            mins = estimate_minutes(d.words)
            length = f"about {mins:.0f} min" if mins >= 1 else "under a minute"
            self._status(f"Narration · {d.words:,} words · {length} · voice {narrator}{warn}",
                         t.WARNING if warn else t.MUTED)
        else:
            cast = "   ".join(f"{role} → {_voice_label(catalog, spec.voice)}"
                              for role, spec in list(check.voices.items())[:6])
            more = f"   +{len(check.voices) - 6}" if len(check.voices) > 6 else ""
            lines = len(check.script.utterances) if check.script else 0
            self._status(f"Dialogue · {len(check.voices)} speakers · {lines} lines   ·   {cast}{more}{warn}",
                         t.WARNING if warn else t.MUTED)
        self.highlight(check)

    def _status(self, text: str, color, link: bool = False) -> None:
        self.status.configure(text=text, text_color=color, cursor="hand2" if link else "")

    # --- highlighting -------------------------------------------------------------------------
    def configure_tags(self) -> None:
        self.editor.tag_config("speaker", foreground=t.pick(t.TAG))
        self.editor.tag_config("comment", foreground=t.pick(t.COMMENT))
        self.editor.tag_config("problem", background=t.pick(t.ERROR_LINE))
        self.editor.tag_raise("sel")

    def highlight(self, check: Check) -> None:
        for tag in ("speaker", "comment", "problem"):
            self.editor.tag_remove(tag, "1.0", "end")
        text = self.text()
        if check.detection.mode == "dialogue" and len(text) <= HIGHLIGHT_LIMIT:
            spans: Dict[str, List[str]] = {"speaker": [], "comment": []}
            for n, line in enumerate(text.splitlines(), 1):
                s = line.lstrip()
                if s.startswith("#") or s.startswith("@"):
                    spans["comment"] += [f"{n}.0", f"{n}.end"]
                else:
                    m = _TAG.match(line)
                    if m:
                        spans["speaker"] += [f"{n}.0", f"{n}.{m.end()}"]
            for tag, idx in spans.items():
                if idx:  # one call for all ranges; CTkTextbox.tag_add only takes a single range
                    self.editor._textbox.tag_add(tag, *idx)
        for n, _ in check.problems:
            if n > 0:
                self.editor.tag_add("problem", f"{n}.0", f"{n}.end+1c")


def _voice_label(catalog: Catalog, name: str) -> str:
    v = catalog.get(name)
    return v.label if v else name
