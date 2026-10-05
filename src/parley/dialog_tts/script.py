"""Parser for the tagged dialogue format.

    # comment
    @voices
    Role = voice-name  [rate=±N%] [pitch=±NHz] [volume=±N%]
    @end
    @lexicon
    Melange = Melohnsch            # spoken form used for every occurrence
    @end
    @sounds
    Anthem1 [= file] [fade_in=S] [fade_out=S] [start=T] [end=T] [volume=±NdB]  # description
    @end
    [Role] text                    one utterance
    [Role slow] text               line modifiers, see MODIFIERS
    [pause 1.5]                    silence in seconds, on its own line
    [sound Anthem1 fade_in=2]      an audio file the user supplies, on its own line (see sounds.py)

See README.md (Multi-voice dialogues) for the full specification.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Mapping, Optional, Tuple, Union

from .voices import PROSODY_KEYS, VoiceSpec, validate_prosody

# Named line modifiers: name -> (rate %, pitch Hz, volume %) deltas relative to the role's voice.
# Only tags rated worthwhile in the German course's prosody study are here; fast/soft/loud were dropped
# (still reachable as rate=/volume= overrides).
MODIFIERS: Dict[str, Tuple[int, int, int]] = {
    "slow": (-20, 0, 0),
}
SLOW_DELTA = -20  # rate delta for the --slow version

_TAG_LINE = re.compile(r"^\[([^\]]*)\]\s*(.*)$")
_ROLE_NAME = re.compile(r"^[A-Za-z_][\w]*$")
_VOICE_LINE = re.compile(r"^([A-Za-z_]\w*)\s*=\s*(\S+)((?:\s+\w+=\S+)*)\s*$")
_LEXICON_LINE = re.compile(r"^(.+?)\s*=\s*(.+?)\s*$")
_INLINE_SAY = re.compile(r"\{([^{}|]+)\|([^{}|]+)\}")
_CAST_LINE = re.compile(r"^#\s*Cast:\s*(.+)$")
_SOUND_LINE = re.compile(r'^([A-Za-z_]\w*)(?:\s*=\s*("[^"]+"|[^\s"]+))?((?:\s+\w+=\S+)*)\s*$')
_SOUND_NOTE = re.compile(r"^(.*?)(?:\s+#\s*(.*))?$")
_SECONDS = re.compile(r"^(\d+(?:\.\d+)?)s?$")
_TIME = re.compile(r"^(?:(\d+):([0-5]\d(?:\.\d+)?)|(\d+(?:\.\d+)?)s?)$")
_DB = re.compile(r"^([+-]?\d+(?:\.\d+)?)dB$", re.I)
BLOCKS = ("voices", "lexicon", "sounds")
SOUND_KEYS = ("fade_in=S", "fade_out=S", "start=T", "end=T", "volume=±NdB")
MAX_FADE_S = 30


class ScriptError(Exception):
    """One or more problems in a tagged file; str() lists them with line numbers."""

    def __init__(self, problems: List[Tuple[str, int, str]]):
        self.problems = problems
        super().__init__("\n".join(f"{p}:{n}: {m}" for p, n, m in problems))


@dataclass(frozen=True)
class Utterance:
    line: int
    role: str
    text: str          # what the learner reads (subtitles)
    speak: str         # what is sent to TTS (lexicon / {shown|spoken} applied)
    spec: VoiceSpec    # role voice with line modifiers applied
    repeat: int = 1
    modifiers: Tuple[str, ...] = ()


@dataclass(frozen=True)
class Pause:
    line: int
    seconds: float


@dataclass(frozen=True)
class SoundOptions:
    """How an inserted audio file plays: times in ms, gain in dB relative to the speech level."""

    fade_in_ms: int = 0
    fade_out_ms: int = 0
    start_ms: int = 0
    end_ms: Optional[int] = None       # None = to the end of the file
    gain_db: float = 0.0

    def describe(self) -> str:
        parts = []
        if self.start_ms or self.end_ms is not None:
            end = fmt_time(self.end_ms) if self.end_ms is not None else "end"
            parts.append(f"{fmt_time(self.start_ms)}-{end}")
        if self.fade_in_ms:
            parts.append(f"fade_in={self.fade_in_ms / 1000:g}s")
        if self.fade_out_ms:
            parts.append(f"fade_out={self.fade_out_ms / 1000:g}s")
        if self.gain_db:
            parts.append(f"volume={self.gain_db:+g}dB")
        return " ".join(parts) or "-"


@dataclass(frozen=True)
class Sound:
    """[sound Name ...]: an audio file the user supplies, played between two lines."""

    line: int
    name: str
    file: str = ""                     # from @sounds; "" = Name.* in the sounds folder
    opts: SoundOptions = SoundOptions()
    caption: str = ""                  # the @sounds description (subtitles), else the name


Segment = Union[Utterance, Pause, Sound]


@dataclass
class Script:
    path: str
    voices: Dict[str, VoiceSpec]
    segments: List[Segment]
    cast: Dict[str, str] = field(default_factory=dict)      # role -> character names
    lexicon: Dict[str, str] = field(default_factory=dict)
    sound_files: Dict[str, Path] = field(default_factory=dict)   # name -> file, set by sounds.attach_sounds

    @property
    def utterances(self) -> List[Utterance]:
        return [s for s in self.segments if isinstance(s, Utterance)]

    @property
    def sounds(self) -> List[Sound]:
        return [s for s in self.segments if isinstance(s, Sound)]

    def label(self, role: str) -> str:
        return self.cast.get(role, role)


def parse_file(path: Union[str, Path], **kwargs) -> Script:
    path = Path(path)
    return parse_text(path.read_text(encoding="utf-8-sig"), str(path), **kwargs)


def parse_text(
    text: str,
    path: str = "<text>",
    *,
    base_voices: Optional[Mapping[str, VoiceSpec]] = None,
    base_lexicon: Optional[Mapping[str, str]] = None,
    continue_speaker: bool = False,
    adjust_voice: Optional[Callable[[str, VoiceSpec], VoiceSpec]] = None,
) -> Script:
    """Parse *text*. Collects every problem and raises one ScriptError listing them all.

    There is no built-in cast: every role must be mapped by *base_voices* or the @voices block.
    *adjust_voice(role, spec)* runs on every role's voice after the @voices block is merged and
    before line modifiers, so a caller's per-speaker choices can win over the file's block.
    """
    voices: Dict[str, VoiceSpec] = dict(base_voices or {})
    header = _Header(lexicon=dict(base_lexicon or {}))   # course lexicon; @lexicon entries win
    raw: List[Tuple[int, str, str, List[str]]] = []  # (line, role, text, modifier tokens)
    pauses: Dict[int, float] = {}
    cues: Dict[int, Tuple[str, Dict[str, object]]] = {}
    order: List[Tuple[str, int]] = []
    problems: List[Tuple[str, int, str]] = []

    def err(n: int, msg: str) -> None:
        problems.append((path, n, msg))

    last_role: Optional[str] = None
    for n, s in _body_lines(text, header, err):
        m = _TAG_LINE.match(s)
        if not m:
            if continue_speaker and last_role:
                raw.append((n, last_role, s, []))
                order.append(("u", n))
            elif continue_speaker:
                err(n, "untagged text before any speaker tag")
            else:
                err(n, f"untagged text (start the line with [Role], or use --continue-speaker): {s[:50]!r}")
            continue
        tokens, body = m.group(1).split(), m.group(2).strip()
        if not tokens:
            err(n, "empty tag []")
            continue
        head = tokens[0]
        if head.lower() == "pause":
            secs = _parse_pause(tokens, body)
            if secs is None:
                err(n, f"bad pause tag, expected [pause N] with N in seconds on its own line: {s!r}")
            else:
                pauses[n] = secs
                order.append(("p", n))
            continue
        if head.lower() == "sound":
            cue = _parse_sound_tag(tokens, body, n, err)
            if cue:
                cues[n] = cue
                order.append(("s", n))
            continue
        if not _ROLE_NAME.match(head):
            err(n, f"malformed tag {head!r}")
            continue
        if not body:
            if head[0].islower():
                err(n, f"unknown control tag [{m.group(1)}] (known: pause, sound)")
            else:
                err(n, f"empty utterance for role {head}")
            continue
        raw.append((n, head, body, tokens[1:]))
        order.append(("u", n))
        last_role = head

    voices.update(header.voices)
    lexicon = header.lexicon
    if adjust_voice is not None:
        voices = {role: adjust_voice(role, spec) for role, spec in voices.items()}
    by_line = {r[0]: r for r in raw}
    segments: List[Segment] = []
    for kind, n in order:
        if kind == "p":
            segments.append(Pause(n, pauses[n]))
            continue
        if kind == "s":
            sound = _make_sound(n, *cues[n], header.sounds, err)
            if sound:
                segments.append(sound)
            continue
        _, role, body, mods = by_line[n]
        if role not in voices:
            known = ", ".join(sorted(voices))
            err(n, f"role {role!r} is not mapped to a voice (known roles: {known})")
            continue
        spec, repeat, mod_names = _apply_modifiers(voices[role], mods, n, err)
        shown, spoken = _apply_lexicon(body, lexicon)
        if not spoken.strip():
            err(n, "empty utterance")
            continue
        segments.append(Utterance(n, role, shown, spoken, spec, repeat, tuple(mod_names)))

    if problems:
        raise ScriptError(sorted(problems, key=lambda p: p[1]))
    return Script(path, voices, segments,
                  {r: " / ".join(names) for r, names in header.cast.items()}, lexicon)


def parse_narration(
    text: str,
    path: str = "<text>",
    *,
    narrator: VoiceSpec,
    split: Callable[[str], List[str]] = lambda t: [t],
    base_lexicon: Optional[Mapping[str, str]] = None,
    role: str = "Narrator",
) -> Script:
    """Parse plain prose that contains [sound Name] / [pause N] lines.

    The prose between two such lines is cut by *split* (the audiobook chunker) and every chunk
    becomes one utterance of *role* read by *narrator*. Comments and @blocks work as in tagged
    files; any other line, also one starting with brackets like "[1] footnote", is prose.
    """
    header = _Header(lexicon=dict(base_lexicon or {}))
    problems: List[Tuple[str, int, str]] = []

    def err(n: int, msg: str) -> None:
        problems.append((path, n, msg))

    order: List[Tuple[str, int, object]] = []    # ("t", first line, prose) / ("p", n, secs) / ("s", n, cue)
    prose: List[str] = []
    first = 0

    def flush() -> None:
        nonlocal prose, first
        if any(p.strip() for p in prose):
            order.append(("t", first, "\n".join(prose)))
        prose, first = [], 0

    for n, s in _body_lines(text, header, err, keep_blank=True):
        m = _TAG_LINE.match(s)
        tokens = m.group(1).split() if m else []
        word = tokens[0].lower() if tokens else ""
        if word == "pause":
            flush()
            secs = _parse_pause(tokens, m.group(2).strip())
            if secs is None:
                err(n, f"bad pause tag, expected [pause N] with N in seconds on its own line: {s!r}")
            else:
                order.append(("p", n, secs))
        elif word == "sound":
            flush()
            cue = _parse_sound_tag(tokens, m.group(2).strip(), n, err)
            if cue:
                order.append(("s", n, cue))
        else:
            first = first or (n if s else 0)
            prose.append(s)
    flush()

    segments: List[Segment] = []
    for kind, n, item in order:
        if kind == "p":
            segments.append(Pause(n, item))
        elif kind == "s":
            sound = _make_sound(n, *item, header.sounds, err)
            if sound:
                segments.append(sound)
        else:
            for chunk in split(item):
                shown, spoken = _apply_lexicon(chunk, header.lexicon)
                if spoken.strip():
                    segments.append(Utterance(n, role, shown, spoken, narrator))
    if problems:
        raise ScriptError(sorted(problems, key=lambda p: p[1]))
    return Script(path, {role: narrator}, segments, {}, header.lexicon)


@dataclass
class _Header:
    """What the comment and @block lines of a file declare."""

    voices: Dict[str, VoiceSpec] = field(default_factory=dict)
    lexicon: Dict[str, str] = field(default_factory=dict)
    sounds: Dict[str, "_SoundDecl"] = field(default_factory=dict)
    cast: Dict[str, List[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class _SoundDecl:
    file: str
    opts: Dict[str, object]
    caption: str


def _body_lines(text: str, header: _Header, err, keep_blank: bool = False) -> Iterator[Tuple[int, str]]:
    """Yield (line number, stripped line) for every line that is not a comment, an @block or
    (unless *keep_blank*) blank; the comments and blocks are collected into *header*."""
    block: Optional[Tuple[str, int]] = None
    for n, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if not s:
            if keep_blank and not block:
                yield n, s
            continue
        if s.startswith("#"):
            m = _CAST_LINE.match(s)
            if m:
                for entry in m.group(1).split(";"):
                    if "=" in entry:
                        name, role = (x.strip() for x in entry.split("=", 1))
                        header.cast.setdefault(role, []).append(name)
            continue
        if block:
            if s == "@end":
                block = None
            elif block[0] == "voices":
                _parse_voice_line(s, n, header.voices, err)
            elif block[0] == "sounds":
                _parse_sound_line(s, n, header.sounds, err)
            else:
                m = _LEXICON_LINE.match(s)
                if m:
                    header.lexicon[m.group(1)] = m.group(2)
                else:
                    err(n, f"bad @lexicon line (expected 'word = spoken form'): {s!r}")
            continue
        if s.startswith("@") and s[1:] in BLOCKS:
            block = (s[1:], n)
            continue
        if s.startswith("@"):
            known = ", ".join(f"@{b}" for b in BLOCKS + ("end",))
            err(n, f"unknown directive {s.split()[0]!r} (known: {known})")
            continue
        yield n, s
    if block:
        err(block[1], f"@{block[0]} block is not closed with @end")


def load_lexicon(path: Union[str, Path]) -> Dict[str, str]:
    """Read a lexicon file: 'word = spoken form' per line, '#' comments, blank lines ignored."""
    out: Dict[str, str] = {}
    for n, line in enumerate(Path(path).read_text(encoding="utf-8-sig").splitlines(), 1):
        s = line.split("#", 1)[0].strip()
        if not s:
            continue
        m = _LEXICON_LINE.match(s)
        if not m:
            raise ScriptError([(str(path), n, f"bad lexicon line (expected 'word = spoken form'): {s!r}")])
        out[m.group(1)] = m.group(2)
    return out


def _parse_voice_line(s: str, n: int, out: Dict[str, VoiceSpec], err) -> None:
    m = _VOICE_LINE.match(s)
    if not m:
        err(n, f"bad @voices line (expected 'Role = voice [rate=..] [pitch=..] [volume=..]'): {s!r}")
        return
    kwargs = {}
    for item in m.group(3).split():
        key, _, value = item.partition("=")
        problem = validate_prosody(key, value)
        if problem:
            err(n, problem)
            return
        kwargs[key] = value
    out[m.group(1)] = VoiceSpec(m.group(2), **kwargs)


def _parse_pause(tokens: List[str], body: str) -> Optional[float]:
    if len(tokens) != 2 or body:
        return None
    try:
        secs = float(tokens[1])
    except ValueError:
        return None
    return secs if 0 <= secs <= 60 else None


def fmt_time(ms: int) -> str:
    """65500 -> '1:05.5', 5000 -> '0:05'."""
    m, rest = divmod(ms, 60_000)
    return f"{m}:{rest / 1000:06.3f}".rstrip("0").rstrip(".")


def _parse_sound_tag(tokens: List[str], body: str, n: int, err) -> Optional[Tuple[str, Dict[str, object]]]:
    """[sound Name opt=value ...] -> (name, options) or None after reporting the problem."""
    if body:
        err(n, f"[sound ...] goes on its own line, with no text after it: {body[:40]!r}")
        return None
    if len(tokens) < 2:
        err(n, "sound tag needs a name: [sound Name]")
        return None
    name = tokens[1]
    if not _ROLE_NAME.match(name):
        err(n, f"bad sound name {name!r} (letters, digits and _, starting with a letter)")
        return None
    opts = _sound_options(tokens[2:], n, err)
    return None if opts is None else (name, opts)


def _parse_sound_line(s: str, n: int, out: Dict[str, _SoundDecl], err) -> None:
    body, caption = _SOUND_NOTE.match(s).groups()
    m = _SOUND_LINE.match(body)
    if not m:
        err(n, f"bad @sounds line (expected 'Name [= file] [option=value ...] [# description]'): {s!r}")
        return
    name, file, options = m.group(1), (m.group(2) or "").strip('"'), m.group(3).split()
    if name in out:
        err(n, f"sound {name} is declared twice in @sounds")
        return
    opts = _sound_options(options, n, err)
    if opts is not None:
        out[name] = _SoundDecl(file, opts, (caption or "").strip())


def _sound_options(items: List[str], n: int, err) -> Optional[Dict[str, object]]:
    """'fade_in=2' ... -> SoundOptions field values; None if any is wrong (each one is reported)."""
    out: Dict[str, object] = {}
    ok = True
    for item in items:
        key, eq, value = item.partition("=")
        problem = None
        if not eq or key not in ("fade_in", "fade_out", "start", "end", "volume"):
            problem = f"unknown sound option {item!r} (known: {', '.join(SOUND_KEYS)})"
        elif key in ("fade_in", "fade_out"):
            m = _SECONDS.match(value)
            if m and float(m.group(1)) <= MAX_FADE_S:
                out[f"{key}_ms"] = round(float(m.group(1)) * 1000)
            else:
                problem = f"{key} must be seconds from 0 to {MAX_FADE_S}, like {key}=2.5, got {value!r}"
        elif key in ("start", "end"):
            ms = _parse_time(value)
            if ms is None:
                problem = f"{key} must be a time like 12.5 or 1:05, got {value!r}"
            else:
                out[f"{key}_ms"] = ms
        else:
            m = _DB.match(value)
            if m and -30 <= float(m.group(1)) <= 10:
                out["gain_db"] = float(m.group(1))
            else:
                problem = f"volume must be dB from -30 to +10, like volume=-6dB, got {value!r}"
        if problem:
            err(n, problem)
            ok = False
    return out if ok else None


def _parse_time(value: str) -> Optional[int]:
    m = _TIME.match(value)
    if not m:
        return None
    if m.group(3) is not None:
        return round(float(m.group(3)) * 1000)
    return int(m.group(1)) * 60_000 + round(float(m.group(2)) * 1000)


def _make_sound(n: int, name: str, tag_opts: Dict[str, object], decls: Mapping[str, _SoundDecl],
                err) -> Optional[Sound]:
    decl = decls.get(name)
    opts = SoundOptions(**{**(decl.opts if decl else {}), **tag_opts})
    if opts.end_ms is not None and opts.end_ms <= opts.start_ms:
        err(n, f"sound {name}: end ({fmt_time(opts.end_ms)}) must come after start ({fmt_time(opts.start_ms)})")
        return None
    return Sound(n, name, decl.file if decl else "", opts, (decl.caption if decl else "") or name)


def _apply_modifiers(spec: VoiceSpec, mods: List[str], n: int, err):
    repeat, names = 1, []
    d_rate = d_pitch = d_vol = 0
    for mod in mods:
        key, eq, value = mod.partition("=")
        if not eq and key in MODIFIERS:
            r, p, v = MODIFIERS[key]
            d_rate, d_pitch, d_vol = d_rate + r, d_pitch + p, d_vol + v
        elif eq and key == "repeat" and value.isdigit() and 1 <= int(value) <= 9:
            repeat = int(value)
        elif eq and key in PROSODY_KEYS:
            problem = validate_prosody(key, value)
            if problem:
                err(n, problem)
                continue
            delta = int(re.sub(r"(%|Hz)$", "", value))
            if key == "rate":
                d_rate += delta
            elif key == "pitch":
                d_pitch += delta
            else:
                d_vol += delta
        else:
            known = ", ".join(list(MODIFIERS) + ["repeat=N", "rate=±N%", "pitch=±NHz", "volume=±N%"])
            err(n, f"unknown line modifier {mod!r} (known: {known})")
            continue
        names.append(mod)
    return spec.shifted(d_rate, d_pitch, d_vol), repeat, names


def _apply_lexicon(body: str, lexicon: Mapping[str, str]) -> Tuple[str, str]:
    """Return (shown, spoken). {shown|spoken} is inline; @lexicon entries are whole words."""
    shown = _INLINE_SAY.sub(lambda m: m.group(1), body)
    spoken = _INLINE_SAY.sub(lambda m: m.group(2), body)
    for word, say in lexicon.items():
        spoken = re.sub(rf"(?<!\w){re.escape(word)}(?!\w)", say, spoken)
    return shown, spoken
