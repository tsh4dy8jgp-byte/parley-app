"""UI-free work for the app: detect the text kind, validate, build the cast, run the engines.

Everything here is plain Python so it can be tested without a display; the window only calls
these functions and runs the coroutines through a `Runner` on a background thread.
"""

from __future__ import annotations

import asyncio
import queue
import re
import threading
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from .audiobook.chunker import split_text
from .audiobook.voices import is_multilingual, multilingual_warning
from .dialog_tts.render import RenderOptions, render_scripts
from .dialog_tts.script import (_ROLE_NAME, _TAG_LINE, ROLE_PATTERN, Script, ScriptError, load_lexicon,
                                parse_narration, parse_text)
from .dialog_tts.sounds import DEFAULT_DIR, SoundFile, attach_sounds, find_sounds
from .dialog_tts.stitch import Gaps
from .dialog_tts.tts import Backend, CachedBackend, EdgeBackend
from .dialog_tts.voices import VoiceSpec, duplicate_voice_warnings

from .catalog import Catalog
from .mp3tags import Tags, read_cover_error
from .settings import Settings

Progress = Callable[[int, int], None]
WORDS_PER_MINUTE = 150


@dataclass
class Detection:
    mode: str                  # "empty" | "narration" | "dialogue"
    roles: List[str]           # speakers in order of first appearance
    words: int
    pinned: List[str] = field(default_factory=list)   # roles given a voice by a @voices block
    sounds: List[str] = field(default_factory=list)   # [sound Name] names in order of first use


_PINNED = re.compile(rf"^({ROLE_PATTERN})\s*=")


def detect(text: str) -> Detection:
    """Dialogue if any line starts with a speaker tag ([A] ..., [Anna slow] ...) or a @voices block.

    [sound Name] lines and a @sounds block don't make a dialogue: plain narration can have them too.
    """
    roles: Dict[str, None] = {}
    pinned: Dict[str, None] = {}
    sounds: Dict[str, None] = {}
    dialogue = False
    block = None
    for line in text.splitlines():
        s = line.strip()
        if block:
            if s == "@end":
                block = None
            elif block == "@voices" and _PINNED.match(s):
                pinned[_PINNED.match(s).group(1)] = None
            continue
        if s in ("@voices", "@lexicon"):
            dialogue, block = True, s
            continue
        if s == "@sounds":
            block = s
            continue
        m = _TAG_LINE.match(s)
        if not m or not m.group(1).split():
            continue
        tokens = m.group(1).split()
        head = tokens[0]
        if head.lower() == "sound":
            if len(tokens) > 1:
                sounds[tokens[1]] = None
        elif head.lower() != "pause" and _ROLE_NAME.match(head):
            roles[head] = None
            dialogue = True
    words = len(text.split())
    if not words:
        return Detection("empty", [], 0)
    return Detection("dialogue" if dialogue else "narration", list(roles), words, list(pinned), list(sounds))


def base_cast(settings: Settings, roles: List[str], catalog: Catalog) -> Dict[str, VoiceSpec]:
    """Role -> voice before the Advanced tab's sliders: automatic for the language.

    Roles without one (no voices for the language) are included only if a voice was picked.
    """
    # Cast over all roles so choosing B's voice never reshuffles the automatic voices of A and C.
    auto = catalog.auto_cast(settings.language, roles)
    cast: Dict[str, VoiceSpec] = {}
    for role in roles:
        sp = settings.speakers.get(role)
        if role in auto:
            cast[role] = auto[role]
        elif sp and sp.voice:
            cast[role] = VoiceSpec(sp.voice)
    return cast


def speaker_adjuster(settings: Settings) -> Callable[[str, VoiceSpec], VoiceSpec]:
    """The Advanced tab applied to a role's voice: a picked voice replaces it, sliders shift it."""
    def adjust(role: str, spec: VoiceSpec) -> VoiceSpec:
        sp = settings.speakers.get(role)
        return sp.spec(spec) if sp else spec
    return adjust


def build_cast(settings: Settings, roles: List[str], catalog: Catalog) -> Dict[str, VoiceSpec]:
    """Role -> voice for a text without a @voices block (Advanced tab included)."""
    adjust = speaker_adjuster(settings)
    return {role: adjust(role, spec) for role, spec in base_cast(settings, roles, catalog).items()}


def lexicon_for(settings: Settings, source: Optional[Path]) -> Dict[str, str]:
    """The lexicon file from settings, else lexicon.txt next to the opened file (like the CLI)."""
    if settings.lexicon:
        return load_lexicon(settings.lexicon)
    if source is not None and (Path(source).parent / "lexicon.txt").is_file():
        return load_lexicon(Path(source).parent / "lexicon.txt")
    return {}


@dataclass
class Check:
    detection: Detection
    problems: List[Tuple[int, str]] = field(default_factory=list)   # (line, message); line 0 = general
    warnings: List[str] = field(default_factory=list)
    voices: Dict[str, VoiceSpec] = field(default_factory=dict)       # role -> voice actually used
    script: Optional[Script] = None
    sounds: List[SoundFile] = field(default_factory=list)            # what the sounds folder has for each name


def sounds_folder(settings: Settings, source: Optional[Path]) -> Optional[Path]:
    """The folder from Advanced → Sounds, else sounds/ next to the opened file (like the CLI)."""
    if settings.sounds_dir:
        return Path(settings.sounds_dir).expanduser()
    return Path(source).parent / DEFAULT_DIR if source is not None else None


def parse(text: str, settings: Settings, catalog: Catalog, source: Optional[Path] = None,
          attach: bool = True) -> Script:
    """Parse a dialogue with the app's cast; raises ScriptError (also for a bad lexicon file or a
    sound whose file is missing, unless *attach* is False)."""
    roles = detect(text).roles
    # Precedence, low to high: automatic < the text's @voices block < Advanced tab.
    script = parse_text(text, safe_name(settings.out_name),
                        base_voices=base_cast(settings, roles, catalog),
                        base_lexicon=lexicon_for(settings, source),
                        continue_speaker=settings.continue_speaker,
                        adjust_voice=speaker_adjuster(settings))
    if attach and script.sounds:
        attach_sounds(script, sounds_folder(settings, source))
    return script


def narration_script(text: str, settings: Settings, catalog: Catalog, source: Optional[Path] = None,
                     attach: bool = True, narrator: Optional[VoiceSpec] = None) -> Script:
    """Plain text with [sound Name] lines as a one-voice script for the dialogue engine: the prose
    between the sounds is cut like an audiobook (chunk size from Advanced) and read by the narrator."""
    script = parse_narration(text, safe_name(settings.out_name),
                             narrator=narrator or narrator_spec(settings, catalog),
                             split=lambda t: split_text(t, max_chars=settings.chunk_size),
                             base_lexicon=lexicon_for(settings, source))
    if attach:
        attach_sounds(script, sounds_folder(settings, source))
    return script


def validate(text: str, settings: Settings, catalog: Catalog, source: Optional[Path] = None) -> Check:
    d = detect(text)
    check = Check(d)
    if d.mode == "narration":
        try:
            voice = narrator_spec(settings, catalog).voice
        except ValueError:          # no voice for the language; generating reports that
            voice = ""
        if is_multilingual(voice):
            check.warnings = [multilingual_warning("the narrator", voice)]
        if d.sounds:
            try:
                s = narration_script(text, settings, catalog, source, attach=False, narrator=VoiceSpec(voice or "?"))
            except ScriptError as e:
                check.problems = [(n, gui_message(msg)) for _, n, msg in e.problems]
                return check
            except OSError as e:
                check.problems = [(0, f"lexicon file: {e}")]
                return check
            check.script = s
            _check_sounds(check, s, settings, source)
    if d.mode != "dialogue":
        return check
    try:
        s = parse(text, settings, catalog, source, attach=False)
    except ScriptError as e:
        check.problems = [(n, gui_message(msg)) for _, n, msg in e.problems]
        check.voices = build_cast(settings, d.roles, catalog)
        return check
    except OSError as e:
        check.problems = [(0, f"lexicon file: {e}")]
        return check
    used = [u.role for u in s.utterances]
    check.script = s
    check.voices = {r: s.voices[r] for r in dict.fromkeys(used)}
    check.warnings = duplicate_voice_warnings(s.voices, used)
    _check_sounds(check, s, settings, source)
    return check


def _check_sounds(check: Check, script: Script, settings: Settings, source: Optional[Path]) -> None:
    """List what the sounds folder has for each sound, and report what's missing as problems."""
    if not script.sounds:
        return
    folder = sounds_folder(settings, source)
    check.sounds = find_sounds(script, folder)
    try:
        attach_sounds(script, folder)
    except ScriptError as e:
        check.problems += [(n, gui_message(msg)) for _, n, msg in e.problems]


# Parser messages name CLI flags; say where the switch is in the window instead.
_CLI_HINTS = {"use --continue-speaker": "turn on “Untagged lines continue the previous speaker” in Advanced",
              "keep the text next to a 'sounds' folder, or choose one with --sounds":
                  "save the text next to a 'sounds' folder, or choose one in Advanced → Sounds"}


def gui_message(msg: str) -> str:
    for cli, gui in _CLI_HINTS.items():
        msg = msg.replace(cli, gui)
    return msg


@dataclass
class LanguageUse:
    """Whether the Standard tab's language picker affects this text, and what decides instead."""

    state: str                  # "free": the picker is used | "fixed": not used | "mismatch": see below
    source: str = ""            # who fixed the voices: "text" (@voices block) or "advanced"
    language: str = ""          # language(s) of the fixed voices, e.g. "German" or "German, English"
    auto_roles: List[str] = field(default_factory=list)   # speakers whose voice follows the picker
    suggested: str = ""         # mismatch: the locale most fixed voices use, e.g. "de-DE"


def language_use(check: Check, settings: Settings, catalog: Catalog) -> LanguageUse:
    """"fixed" when every voice comes from the text or Advanced; "mismatch" when the text's voices
    are in another language than the picker, which would give the automatic speakers foreign voices."""
    d = check.detection
    if d.mode == "narration" and settings.narrator.voice:
        return LanguageUse("fixed", "advanced", _languages([settings.narrator.voice], catalog))
    if d.mode != "dialogue":
        return LanguageUse("free")
    explicit = {r for r in d.roles if settings.speakers.get(r) and settings.speakers[r].voice}
    fixed = [r for r in d.roles if r in explicit or r in d.pinned]
    auto = [r for r in d.roles if r not in fixed]
    names = [check.voices[r].voice for r in fixed if r in check.voices]
    if not names:
        return LanguageUse("free", auto_roles=auto)
    source = "text" if any(r in d.pinned and r not in explicit for r in fixed) else "advanced"
    language = _languages(names, catalog)
    if not auto:
        return LanguageUse("fixed", source, language)
    suggested = Counter(_voice_locale(n, catalog) for n in names).most_common(1)[0][0]
    if _base(suggested) != _base(settings.language):
        return LanguageUse("mismatch", source, language, auto, suggested)
    return LanguageUse("free", auto_roles=auto)


def _voice_locale(name: str, catalog: Catalog) -> str:
    v = catalog.get(name)
    return v.locale if v else name.rsplit("-", 1)[0]


def _base(locale: str) -> str:
    return locale.split("-")[0].lower()


def _languages(names: List[str], catalog: Catalog) -> str:
    """'German' for de-AT + de-CH voices; 'German, English' when they differ (first-seen order)."""
    out: Dict[str, None] = {}
    for name in names:
        label = catalog.language_label(_voice_locale(name, catalog))
        out[label.split(" (")[0].split(" · ")[0]] = None
    return ", ".join(out)


def tags_for(settings: Settings) -> Tags:
    """MP3 tags from the settings; the title stays empty (= the file name) unless one was set."""
    return Tags(title=settings.title, artist=settings.artist, album=settings.album,
                album_artist=settings.album_artist, genre=settings.genre, year=settings.year,
                track=settings.track, comment=settings.comment, cover=settings.cover)


def cover_problem(settings: Settings) -> Optional[str]:
    return read_cover_error(tags_for(settings))


def render_options(settings: Settings) -> RenderOptions:
    return RenderOptions(
        gaps=Gaps(change_ms=settings.gap_change, same_ms=settings.gap_same, repeat_ms=settings.gap_repeat),
        shadow_factor=settings.shadow_factor, shadow=settings.shadow, slow=settings.slow,
        srt=settings.srt, lrc=settings.lrc, clips=settings.clips, wav=settings.wav,
        loudness=settings.loudness, target=settings.target, tags=tags_for(settings),
    )


_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_KNOWN_EXT = (".mp3", ".wav", ".srt", ".lrc", ".txt", ".md", ".markdown", ".tagged")


def safe_name(name: str) -> str:
    """A file stem usable by the renderer (it cuts names at the first dot)."""
    s = _UNSAFE.sub("_", name.strip())
    while s.lower().endswith(_KNOWN_EXT):
        s = s.rsplit(".", 1)[0]
    s = s.replace(".", "_").strip()
    return s or "untitled"


def planned_outputs(settings: Settings, mode: str) -> List[Path]:
    """The files a run would write (to warn before overwriting); the clips folder is not listed."""
    out, stem = Path(settings.out_dir).expanduser(), safe_name(settings.out_name)
    if mode != "dialogue":
        return [out / f"{stem}.mp3"]
    suffixes = [""] + [".shadow"] * settings.shadow + [".slow"] * settings.slow
    exts = [".mp3"] + [".wav"] * settings.wav + [".srt"] * settings.srt + [".lrc"] * settings.lrc
    return [out / f"{stem}{sfx}{ext}" for sfx in suffixes for ext in exts]


# --- several files: one MP3 each ------------------------------------------------------------------

BATCH_STEPS = 1000      # progress of one file in a batch; the app turns (done, total) into "file k of n"


@dataclass
class BatchItem:
    path: Path
    text: str
    name: str          # output file stem, unique within the batch


def batch_items(files: List[Tuple[Path, str]]) -> List[BatchItem]:
    """(path, text) pairs as batch items; two files with the same stem (a.txt, a.md) get _2, _3, …"""
    items: List[BatchItem] = []
    used: set = set()
    for path, text in files:
        base = safe_name(Path(path).name)
        name, n = base, 1
        while name.casefold() in used:
            n += 1
            name = f"{base}_{n}"
        used.add(name.casefold())
        items.append(BatchItem(Path(path), text, name))
    return items


def item_settings(settings: Settings, item: BatchItem) -> Settings:
    """The shared settings for one file: only the output name and the title (= the file name) change."""
    return replace(settings, out_name=item.name, title="")


def batch_todo(items: List[BatchItem]) -> List[Tuple[BatchItem, str]]:
    """The items that have text, with their mode ("narration" | "dialogue")."""
    found = [(i, detect(i.text).mode) for i in items]
    return [(i, mode) for i, mode in found if mode != "empty"]


def needs_mixing(items: List[BatchItem]) -> bool:
    """True if any file is a dialogue or has [sound Name] lines (those need ffmpeg)."""
    return any(mode == "dialogue" or detect(i.text).sounds for i, mode in batch_todo(items))


def batch_summary(items: List[BatchItem]) -> str:
    modes = Counter(mode for _, mode in batch_todo(items))
    parts = [f"{n} {mode}{'s' * (n > 1)}" for mode, n in modes.items()]
    return f"{len(items)} files" + (" · " + ", ".join(parts) if parts else "")


def validate_batch(items: List[BatchItem], settings: Settings, catalog: Catalog) -> Check:
    """Check every file on its own. The result describes the joined texts (for the speakers and sounds
    panels), but its problems are those of the single files, named by file."""
    check = validate("\n\n".join(i.text for i in items), settings, catalog, items[0].path)
    check.problems = []
    for item in items:
        one = validate(item.text, item_settings(settings, item), catalog, item.path)
        for n, msg in one.problems:
            check.problems.append((0, f"{item.path.name}{f', line {n}' if n else ''}: {msg}"))
    return check


def planned_batch_outputs(items: List[BatchItem], settings: Settings) -> List[Path]:
    return [p for item, mode in batch_todo(items) for p in planned_outputs(item_settings(settings, item), mode)]


async def run_batch(items: List[BatchItem], settings: Settings, catalog: Catalog,
                    on_progress: Optional[Progress] = None,
                    backend: Optional[Backend] = None) -> List[Tuple[Path, int]]:
    """One MP3 per file, in order, all with the same settings. Progress is (done, total) with
    BATCH_STEPS per file. A failure names the file; the files before it stay written."""
    todo = batch_todo(items)
    if not todo:
        raise ValueError("Every file is empty.")
    backend = backend or make_backend(settings)
    written: List[Tuple[Path, int]] = []
    total = len(todo) * BATCH_STEPS
    for k, (item, mode) in enumerate(todo):
        def progress(done: int, n: int, k: int = k) -> None:
            if on_progress:
                on_progress(k * BATCH_STEPS + done * BATCH_STEPS // max(n, 1), total)

        s = item_settings(settings, item)
        try:
            if mode == "dialogue":
                written += await run_dialogue(item.text, s, catalog, progress, backend, source=item.path)
            else:
                written += await run_narration(item.text, s, catalog, progress, source=item.path, backend=backend)
        except ScriptError as e:
            raise RuntimeError(f"{item.path.name}: {gui_message(str(e).splitlines()[0])}") from e
        except Exception as e:  # noqa: BLE001 - reported to the user
            raise RuntimeError(f"{item.path.name}: {e}") from e
    return written


def estimate_minutes(words: int) -> float:
    return words / WORDS_PER_MINUTE


def make_backend(settings: Settings, concurrency: Optional[int] = None) -> Backend:
    return CachedBackend(EdgeBackend(concurrency=concurrency or settings.concurrency),
                         Path(settings.cache_dir).expanduser())


async def run_dialogue(text: str, settings: Settings, catalog: Catalog, on_progress: Optional[Progress] = None,
                       backend: Optional[Backend] = None,
                       source: Optional[Path] = None) -> List[Tuple[Path, int]]:
    script = parse(text, settings, catalog, source)
    return await render_scripts([script], backend or make_backend(settings),
                                Path(settings.out_dir).expanduser(), render_options(settings), on_progress)


async def run_narration(text: str, settings: Settings, catalog: Catalog, on_progress: Optional[Progress] = None,
                        source: Optional[Path] = None, backend: Optional[Backend] = None) -> List[Tuple[Path, int]]:
    """One MP3 read by the narrator. Text with [sound Name] lines is mixed by the dialogue engine
    (needs ffmpeg); the extra outputs stay dialogue-only, so the files are those planned_outputs lists."""
    from .audiobook.builder import build_audiobook

    if detect(text).sounds:
        script = narration_script(text, settings, catalog, source)
        opts = replace(render_options(settings), srt=False, lrc=False, shadow=False, slow=False, clips=False,
                       wav=False)
        return await render_scripts([script], backend or make_backend(settings),
                                    Path(settings.out_dir).expanduser(), opts, on_progress)
    spec = narrator_spec(settings, catalog)
    out = Path(settings.out_dir).expanduser() / f"{safe_name(settings.out_name)}.mp3"
    await build_audiobook(text, spec.voice, out, rate=spec.rate, volume=spec.volume, pitch=spec.pitch,
                          max_chars=settings.chunk_size, concurrency=settings.concurrency, tags=tags_for(settings),
                          on_progress=on_progress)
    return [(out, 0)]


def narrator_spec(settings: Settings, catalog: Catalog) -> VoiceSpec:
    gender = settings.gender if settings.gender in ("Female", "Male") else None
    auto = None if settings.narrator.voice else VoiceSpec(catalog.pick_narrator(settings.language, gender))
    return settings.narrator.spec(auto)


async def preview(text: str, spec: VoiceSpec, settings: Settings) -> bytes:
    return await make_backend(settings, concurrency=1).synth(text, spec)


# --- background execution -----------------------------------------------------------------------

@dataclass
class Event:
    kind: str        # what the runner was started for, e.g. "generate", "preview"
    type: str        # "progress" | "done" | "error" | "cancelled"
    payload: Any = None


class Runner:
    """Runs one coroutine at a time on its own thread + event loop; reports through a queue.

    Tk widgets must only be touched from the main thread, so the window polls `events`.
    """

    def __init__(self, events: "queue.Queue[Event]", kind: str):
        self.events = events
        self.kind = kind
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._task: Optional[asyncio.Task] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, factory: Callable[[Progress], Awaitable[Any]]) -> None:
        if self.busy:
            raise RuntimeError(f"{self.kind} is already running")
        loop = asyncio.new_event_loop()
        progress = lambda done, total: self.events.put(Event(self.kind, "progress", (done, total)))  # noqa: E731
        task = loop.create_task(factory(progress))
        self._loop, self._task = loop, task

        def run() -> None:
            try:
                result = loop.run_until_complete(task)
                self.events.put(Event(self.kind, "done", result))
            except asyncio.CancelledError:
                self.events.put(Event(self.kind, "cancelled"))
            except Exception as e:  # noqa: BLE001 - reported to the user
                self.events.put(Event(self.kind, "error", e))
            finally:
                loop.close()

        self._thread = threading.Thread(target=run, name=f"parley-{self.kind}", daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        loop, task = self._loop, self._task
        if self.busy and loop is not None and task is not None:
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:  # loop closed in the meantime
                pass

    def join(self, timeout: Optional[float] = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)
