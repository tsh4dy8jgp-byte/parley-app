"""Parley main window: header, Standard / Advanced tabs, dialogue guide and the action bar."""

from __future__ import annotations

import base64
import os
import queue
import struct
import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import List, Optional, Tuple

import customtkinter as ctk

from .dialog_tts.script import ScriptError

try:  # optional: file drag and drop (pip extra "gui")
    from tkinterdnd2 import COPY, DND_FILES, TkinterDnD
except ImportError:
    TkinterDnD = None

from . import catalog as cat
from . import jobs, system, textfiles
from . import settings as st
from . import theme as t
from . import widgets as w
from .advanced_tab import AdvancedTab
from .guide import example_for
from .guide_panel import GuidePanel
from .model import Model
from .standard_tab import StandardTab

PREVIEW_LINES = {
    "en": "Hello! This is how my voice sounds.",
    "de": "Hallo! So klingt meine Stimme.",
    "fr": "Bonjour ! Voici comment sonne ma voix.",
    "es": "¡Hola! Así suena mi voz.",
    "ru": "Привет! Вот так звучит мой голос.",
}
STATUS_COLORS = {"info": t.MUTED, "success": t.SUCCESS, "error": t.DANGER}
TAGLINE = "From one narrator to a full cast"
ICON = Path(__file__).with_name("parley.ico")


# The root window needs tkinterdnd2's mixin to accept drops; other widgets are patched by the package.
_BASES = (ctk.CTk, TkinterDnD.DnDWrapper) if TkinterDnD else (ctk.CTk,)


class ParleyApp(*_BASES):
    def __init__(self, fetch_catalog: bool = True):
        settings = st.load()
        t.apply(settings.appearance)
        super().__init__(fg_color=t.BG)
        self.title("Parley")
        self._set_icon()
        self.geometry("1200x800")
        self.minsize(980, 640)

        self.model = Model(self, settings)
        self.catalog = cat.load_cached(self._voices_file())
        self.events: "queue.Queue[jobs.Event]" = queue.Queue()
        self.gen = jobs.Runner(self.events, "generate")
        self.source: Optional[Path] = None           # file the text was opened from / saved to
        self._save_as: Optional[Path] = None         # Save… default; None once the text differs from a file
        self._drop_hide_job = None
        self.check: Optional[jobs.Check] = None
        self.last_output: Optional[Path] = None
        self._player = None
        self._validate_job = None
        self._t0 = 0.0
        self._unit = "lines"
        self._mixing = True          # the run mixes clips after synthesis (dialogues, text with sounds)

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._build_header()
        self._build_body()
        self._build_action_bar()
        self.dnd = self._enable_drop()
        self.standard.set_drop_enabled(self.dnd)

        self.standard.set_text(st.load_draft())
        self.standard.set_languages(self.catalog)
        self._bind_keys()
        self.model.on_change(self.schedule_validate)
        self.model.on_change(self._language_changed, ["language", "gender"])
        self.model.on_change(self._appearance_changed, ["appearance"])
        self.model.on_change(self.advanced.sync_loudness, ["loudness"])
        self.model.on_change(self.advanced.refresh_cache_info, ["cache_dir"])
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self._language_changed()
        self.validate_now()
        self.after(80, self._poll)
        if fetch_catalog:
            jobs.Runner(self.events, "catalog").start(lambda _p: cat.fetch(self._voices_file()))

    # --- layout -------------------------------------------------------------------------------
    def _set_icon(self) -> None:
        """Window, Dock and taskbar icon: Windows reads the .ico, elsewhere Tk gets its PNG images."""
        try:
            if sys.platform == "win32":
                self.iconbitmap(str(ICON))
            else:
                self._icons = [tk.PhotoImage(master=self, data=base64.b64encode(png).decode("ascii"))
                               for png in ico_pngs(ICON.read_bytes())]
                self.iconphoto(True, *self._icons)
        except (OSError, ValueError, struct.error, tk.TclError):
            pass  # the default icon is fine; never fail to open over this

    def _build_header(self) -> None:
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=24, pady=(18, 6))
        head.grid_columnconfigure(1, weight=1)
        title = ctk.CTkFrame(head, fg_color="transparent")
        title.grid(row=0, column=0, sticky="w")
        w.label(title, "Parley", size=22, weight="bold").grid(row=0, column=0, sticky="w")
        w.caption(title, TAGLINE).grid(row=1, column=0, sticky="w")
        self.badge = w.Badge(head)
        self.badge.grid(row=0, column=2, sticky="e", padx=(0, 10))
        self.guide_button = w.ghost(head, "?  Guide", self.toggle_guide, width=96, height=32)
        self.guide_button.grid(row=0, column=3, sticky="e")

    def _build_body(self) -> None:
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=(16, 24))
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)
        self.tabs = ctk.CTkTabview(
            body, anchor="w", fg_color="transparent", border_width=0, corner_radius=t.RADIUS,
            segmented_button_fg_color=t.FIELD, segmented_button_selected_color=t.SURFACE,
            segmented_button_selected_hover_color=t.SURFACE, segmented_button_unselected_color=t.FIELD,
            segmented_button_unselected_hover_color=t.FIELD_HOVER, text_color=t.TEXT)
        self.tabs.grid(row=0, column=0, sticky="nsew")
        for name in ("Standard", "Advanced"):
            tab = self.tabs.add(name)
            tab.grid_columnconfigure(0, weight=1)
            tab.grid_rowconfigure(0, weight=1)
        self.tabs._segmented_button.configure(font=t.font(13, "bold"), height=32)
        self.standard = StandardTab(self.tabs.tab("Standard"), self)
        self.standard.grid(row=0, column=0, sticky="nsew")
        self.advanced = AdvancedTab(self.tabs.tab("Advanced"), self)
        self.advanced.grid(row=0, column=0, sticky="nsew")
        self.guide = GuidePanel(body, on_insert=self.insert_example, on_close=self.toggle_guide)
        self._show_guide(self.model["guide_open"].get())

    def _build_action_bar(self) -> None:
        bar = ctk.CTkFrame(self, fg_color=t.SURFACE, corner_radius=0, border_width=0, height=64)
        bar.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        bar.grid_columnconfigure(1, weight=1)
        ctk.CTkFrame(bar, fg_color=t.BORDER, height=1, corner_radius=0).grid(row=0, column=0, columnspan=7,
                                                                           sticky="ew")
        self.progress = ctk.CTkProgressBar(bar, width=180, height=6, corner_radius=3, progress_color=t.ACCENT,
                                           fg_color=t.FIELD)
        self.progress.set(0)
        self.status = w.label(bar, "", size=12, color=t.MUTED)
        self.status.grid(row=1, column=1, sticky="ew", padx=(24, 12), pady=14)
        self.play_button = w.ghost(bar, "▶  Play", self.play_output, width=80, height=34)
        self.folder_button = w.ghost(bar, "Show in folder", self.reveal_output, width=120, height=34)
        self.cancel_button = w.ghost(bar, "Cancel", self.cancel, width=84, height=34)
        self.generate_button = w.primary(bar, f"Generate   {t.MOD_LABEL}↵", self.generate, width=160, height=38)
        self.generate_button.grid(row=1, column=6, sticky="e", padx=(8, 24), pady=12)

    def _show_guide(self, show: bool) -> None:
        if show:
            self.guide.grid(row=0, column=1, sticky="nsew", padx=(14, 0), pady=(46, 0))
        else:
            self.guide.grid_remove()
        self.guide_button.configure(fg_color=t.ACCENT_SOFT if show else "transparent",
                                    text_color=t.ACCENT if show else t.TEXT)

    def toggle_guide(self) -> None:
        show = not self.model["guide_open"].get()
        self.model["guide_open"].set(show)
        self._show_guide(show)

    def _bind_keys(self) -> None:
        def key(seq: str, fn) -> None:
            self.bind_all(seq, lambda _e: (fn(), "break")[1])

        key(f"<{t.MOD}-Return>", self.generate)
        key(f"<{t.MOD}-o>", self.open_file)
        key(f"<{t.MOD}-s>", self.save_file)
        key("<F1>", self.toggle_guide)
        key("<Escape>", self.cancel)
        # The editor would insert a newline for ⌘↵ before the global binding runs.
        self.standard.editor.bind(f"<{t.MOD}-Return>", lambda _e: (self.generate(), "break")[1])

    # --- drag and drop ------------------------------------------------------------------------
    def _enable_drop(self) -> bool:
        """Accept files dropped anywhere on the window; False if tkdnd is not available."""
        if TkinterDnD is None:
            return False
        try:
            TkinterDnD._require(self)
            for target in (self, self.standard.editor._textbox):
                target.drop_target_register(DND_FILES)
                target.dnd_bind("<<DropEnter>>", self._drop_enter)
                target.dnd_bind("<<DropPosition>>", lambda _e: COPY)
                target.dnd_bind("<<DropLeave>>", self._drop_leave)
                target.dnd_bind("<<Drop>>", self._drop)
        except (tk.TclError, RuntimeError):
            return False
        return True

    def _drop_enter(self, _event):
        if self._drop_hide_job is not None:
            self.after_cancel(self._drop_hide_job)
            self._drop_hide_job = None
        self.standard.show_drop_hint(True)
        return COPY

    def _drop_leave(self, _event):
        # Moving from the window onto the editor is a leave + enter; wait so the hint does not flicker.
        self._drop_hide_job = self.after(80, lambda: self.standard.show_drop_hint(False))
        return COPY

    def _drop(self, event):
        self._drop_hide_job = None
        self.standard.show_drop_hint(False)
        self.open_paths(self.tk.splitlist(event.data))
        return COPY

    # --- status -------------------------------------------------------------------------------
    def set_status(self, text: str, kind: str = "info") -> None:
        self.status.configure(text=text, text_color=STATUS_COLORS[kind])

    def _set_running(self, running: bool) -> None:
        self.generate_button.configure(state="disabled" if running else "normal")
        for b in (self.play_button, self.folder_button):
            b.grid_remove()
        if running:
            self.progress.set(0)
            self.progress.grid(row=1, column=0, sticky="w", padx=(24, 0))
            self.cancel_button.grid(row=1, column=5, sticky="e")
        else:
            self.progress.grid_remove()
            self.cancel_button.grid_remove()

    # --- validation ---------------------------------------------------------------------------
    def schedule_validate(self) -> None:
        if self._validate_job is not None:
            self.after_cancel(self._validate_job)
        self._validate_job = self.after(350, self.validate_now)

    def validate_now(self) -> None:
        self._validate_job = None
        s = self.model.settings()
        check = jobs.validate(self.standard.text(), s, self.catalog, self.source)
        self.check = check
        self.standard.show_check(check, self.catalog, s, self._narrator_label(s))
        self.standard.show_language_use(jobs.language_use(check, s, self.catalog), check.detection.mode)
        self.advanced.set_roles(check.detection.roles, check.voices, check.detection.pinned, self.catalog)
        self.advanced.set_mode(check.detection.mode)
        self.advanced.set_sounds(check.sounds, jobs.sounds_folder(s, self.source))
        d = check.detection
        sounds = f" · {len(d.sounds)} sound{'s' * (len(d.sounds) != 1)}" if d.sounds else ""
        if d.mode == "empty":
            self.badge.set("")
        elif check.problems:
            self.badge.set(f"{len(check.problems)} problem{'s' * (len(check.problems) > 1)}", "danger")
        elif d.mode == "dialogue":
            self.badge.set(f"Dialogue · {len(d.roles)} speaker{'s' * (len(d.roles) != 1)}{sounds}", "accent")
        else:
            self.badge.set(f"Narration{sounds}", "neutral")

    def _narrator_label(self, s: st.Settings) -> str:
        try:
            v = jobs.narrator_spec(s, self.catalog).voice
        except ValueError:
            return "none for this language"
        voice = self.catalog.get(v)
        return voice.label if voice else v

    def _language_changed(self) -> None:
        s = self.model.settings()
        self.standard.show_language()
        self.advanced.refresh_voices(self.catalog, self._narrator_label(s))

    def _appearance_changed(self) -> None:
        t.apply(self.model["appearance"].get())
        self.after(50, self._retint)

    def _retint(self) -> None:
        self.standard.configure_tags()
        if self.check:
            self.standard.highlight(self.check)

    # --- text actions -------------------------------------------------------------------------
    def open_file(self) -> None:
        paths = filedialog.askopenfilenames(title="Open text", filetypes=textfiles.FILETYPES)
        if paths:
            self.open_paths(paths)

    def open_paths(self, paths) -> None:
        """Open .txt / .md files (several are joined in name order); used by Open… and drag and drop."""
        loaded = textfiles.load(paths)
        if not loaded.used:
            self.set_status(loaded.summary(), "error")
            return
        self.source = loaded.used[0]
        as_is = len(loaded.used) == 1 and not loaded.cleaned
        self._save_as = self.source if as_is else None
        self.model["out_name"].set(jobs.safe_name(self.source.name))
        self.standard.set_text(loaded.text)
        self.tabs.set("Standard")
        self.validate_now()
        self.set_status(loaded.summary(), "info")

    def save_file(self) -> None:
        initial = self._save_as.name if self._save_as else f"{jobs.safe_name(self.model['out_name'].get())}.txt"
        path = filedialog.asksaveasfilename(title="Save text", defaultextension=".txt", initialfile=initial,
                                            initialdir=str(self.source.parent) if self.source else None,
                                            filetypes=[("Text", "*.txt"), ("All files", "*")])
        if not path:
            return
        try:
            Path(path).write_text(self.standard.text(), encoding="utf-8")
        except OSError as e:
            messagebox.showerror("Save text", f"Could not save {path}:\n{e}", parent=self)
            return
        self.source = self._save_as = Path(path)
        self.set_status(f"Saved {self.source.name}", "success")

    def insert_example(self) -> None:
        example = example_for(self.model["language"].get())
        current = self.standard.text()
        if current.strip() and current != example and not messagebox.askyesno(
                "Insert example", "Replace the text in the editor with the example dialogue?\n"
                                  f"(You can undo with {t.MOD_LABEL}Z.)", parent=self):
            return
        self.source = self._save_as = None
        if self.model["out_name"].get() in ("", "untitled"):
            self.model["out_name"].set("example_dialogue")
        self.standard.set_text(example)
        self.tabs.set("Standard")
        self.validate_now()

    def clear_text(self) -> None:
        self.standard.set_text("")
        self.source = self._save_as = None
        self.standard.editor.focus_set()

    def reset_settings(self) -> None:
        if not messagebox.askyesno("Reset settings", "Reset every setting to its default? Your text stays.",
                                   parent=self):
            return
        guide_open = self.model["guide_open"].get()
        self.model.load(st.Settings(guide_open=guide_open))
        self.advanced.sync_loudness()
        self.set_status("Settings reset to defaults.", "info")

    # --- generate -----------------------------------------------------------------------------
    def generate(self) -> None:
        if self.gen.busy:
            return
        text = self.standard.text()
        s = self.model.settings()
        check = jobs.validate(text, s, self.catalog, self.source)
        mode = check.detection.mode
        if mode == "empty":
            self.set_status("Nothing to read yet: paste some text or insert the example dialogue.", "error")
            return
        mixing = mode == "dialogue" or bool(check.detection.sounds)
        if mixing:
            hint = system.dialogue_ready()
            if hint:
                self.set_status(hint, "error")
                return
            if check.problems:
                self.validate_now()
                self.tabs.set("Standard")
                line = check.problems[0][0]
                self.standard.goto_line(line)
                self.set_status(f"Fix the problems in the text first{f' (line {line})' if line else ''}.", "error")
                return
        if (problem := jobs.cover_problem(s)):
            self.tabs.set("Advanced")
            self.set_status(f"Cover image: {problem}", "error")
            return
        out_dir = Path(s.out_dir).expanduser()
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self.set_status(f"Can't create the output folder: {e}", "error")
            return
        existing = [p for p in jobs.planned_outputs(s, mode) if p.exists()]
        if existing and not messagebox.askyesno(
                "Replace files?", f"These files already exist in {out_dir}:\n\n"
                                  + "\n".join(p.name for p in existing[:6])
                                  + ("\n…" if len(existing) > 6 else "") + "\n\nReplace them?", parent=self):
            return
        st.save(s)
        source = self.source
        self._mixing = mixing
        if mode == "dialogue":
            self._unit = "lines"
            self.gen.start(lambda p: jobs.run_dialogue(text, s, self.catalog, p, source=source))
        else:
            self._unit = "parts"
            self.gen.start(lambda p: jobs.run_narration(text, s, self.catalog, p, source=source))
        self._t0 = time.perf_counter()
        self._set_running(True)
        self.set_status("Connecting to the voice service…", "info")

    def cancel(self) -> None:
        if self.gen.busy:
            self.gen.cancel()
            self.set_status("Cancelling…", "info")

    def play_output(self) -> None:
        if self.last_output and self.last_output.exists():
            system.open_path(self.last_output)

    def reveal_output(self) -> None:
        if self.last_output:
            system.reveal(self.last_output)

    # --- previews -----------------------------------------------------------------------------
    def preview_speaker(self, role: str) -> None:
        # Check afresh (not the debounced result) so a slider moved a moment ago is heard.
        s = self.model.settings()
        check = jobs.validate(self.standard.text(), s, self.catalog, self.source)
        spec = check.voices.get(role)
        if spec is None:                                      # a speaker added in Advanced, not in the text
            roles = check.detection.roles + [role]
            spec = jobs.build_cast(s, roles, self.catalog).get(role)
        if spec is None:
            self.set_status(f"No voice available for {role} in this language.", "error")
            return
        line = None
        if check.script:
            line = next((u.speak for u in check.script.utterances if u.role == role), None)
        self._preview(line or self._sample_line(), spec, role)

    def preview_narrator(self) -> None:
        s = self.model.settings()
        try:
            spec = jobs.narrator_spec(s, self.catalog)
        except ValueError as e:
            self.set_status(str(e), "error")
            return
        text = self.standard.text().strip()
        sample = text[:220].rsplit(" ", 1)[0] if self.check and self.check.detection.mode == "narration" else ""
        self._preview(sample or self._sample_line(), spec, "narrator")

    def _sample_line(self) -> str:
        return PREVIEW_LINES.get(self.model["language"].get().split("-")[0].lower(), PREVIEW_LINES["en"])

    def _preview(self, text: str, spec, who: str) -> None:
        system.stop(self._player)
        s = self.model.settings()
        self.set_status(f"Preview: {who} · {spec.voice}…", "info")
        jobs.Runner(self.events, "preview").start(lambda _p: jobs.preview(text, spec, s))

    # --- events from background runners ------------------------------------------------------
    def _poll(self) -> None:
        try:
            while True:
                self._handle(self.events.get_nowait())
        except queue.Empty:
            pass
        self.after(80, self._poll)

    def _handle(self, ev: jobs.Event) -> None:
        if ev.kind == "generate":
            self._handle_generate(ev)
        elif ev.kind == "catalog" and ev.type == "done":
            self.catalog = ev.payload
            self.standard.set_languages(self.catalog)
            self._language_changed()
            self.validate_now()
        elif ev.kind == "catalog" and ev.type == "error" and self.catalog.offline:
            self.set_status("Offline: only a few voices are available until the voice list can be loaded.",
                            "error")
        elif ev.kind == "preview" and ev.type == "done":
            self._player = system.play_bytes(ev.payload)
            self.set_status("", "info")
        elif ev.kind == "preview" and ev.type == "error":
            self.set_status(f"Preview failed: {_first_line(ev.payload)}", "error")

    def _handle_generate(self, ev: jobs.Event) -> None:
        if ev.type == "progress":
            done, total = ev.payload
            self.progress.set(done / max(total, 1))
            if done == total and self._mixing:
                self.set_status(f"Synthesised {total} {self._unit} · mixing audio…", "info")
            else:
                self.set_status(f"Synthesising {done} / {total} {self._unit}…", "info")
            return
        self._set_running(False)
        if ev.type == "done":
            written: List[Tuple[Path, int]] = ev.payload
            mp3s = [p for p, _ in written if p.suffix == ".mp3"]
            self.last_output = mp3s[0] if mp3s else (written[0][0] if written else None)
            secs = time.perf_counter() - self._t0
            n = len(written)
            self.set_status(f"Done in {secs:.1f} s · {n} file{'s' * (n != 1)} in {self.last_output.parent}"
                            if self.last_output else "Done.", "success")
            self.play_button.grid(row=1, column=3, sticky="e", padx=(0, 8))
            self.folder_button.grid(row=1, column=4, sticky="e", padx=(0, 8))
            self.advanced.refresh_cache_info()
        elif ev.type == "cancelled":
            self.set_status("Cancelled.", "info")
        else:
            err = ev.payload
            if isinstance(err, ScriptError):
                self.validate_now()
            self.set_status(f"Failed: {jobs.gui_message(_first_line(err))}", "error")

    # --- shutdown -----------------------------------------------------------------------------
    def on_close(self) -> None:
        if self.gen.busy:
            if not messagebox.askyesno("Quit", "A file is still being generated. Stop it and quit?", parent=self):
                return
            self.gen.cancel()
            self.gen.join(3)
        try:
            st.save(self.model.settings())
            st.save_draft(self.standard.text())
        except OSError:
            pass
        system.stop(self._player)
        self.destroy()

    def _voices_file(self) -> Path:
        return Path(self.model["cache_dir"].get()).expanduser() / "voices.json"


def _first_line(err) -> str:
    text = str(err).strip() or type(err).__name__
    return text.splitlines()[0][:200]


def ico_pngs(data: bytes) -> List[bytes]:
    """The PNG images inside an .ico file, largest first (BMP-encoded entries are skipped)."""
    reserved, kind, count = struct.unpack_from("<HHH", data, 0)
    if reserved != 0 or kind != 1:
        raise ValueError("not an .ico file")
    images = []
    for i in range(count):
        width, *_, size, offset = struct.unpack_from("<BBBBHHII", data, 6 + 16 * i)
        png = data[offset:offset + size]
        if png.startswith(b"\x89PNG"):
            images.append((width or 256, png))   # width 0 means 256
    return [png for _, png in sorted(images, key=lambda x: -x[0])]


def main() -> None:
    if not os.environ.get("PARLEY_HOME"):   # a custom home is deliberate: don't move the old files into it
        st.migrate_legacy()
    ParleyApp().mainloop()


if __name__ == "__main__":
    main()
