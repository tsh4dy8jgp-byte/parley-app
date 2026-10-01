"""Parser for the tagged dialogue format.

    # comment
    @voices
    Role = voice-name  [rate=±N%] [pitch=±NHz] [volume=±N%]
    @end
    @lexicon
    Melange = Melohnsch            # spoken form used for every occurrence
    @end
    [Role] text                    one utterance
    [Role slow] text               line modifiers, see MODIFIERS
    [pause 1.5]                    silence in seconds, on its own line

See README.md (Multi-voice dialogues) for the full specification.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional, Tuple, Union

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


Segment = Union[Utterance, Pause]


@dataclass
class Script:
    path: str
    voices: Dict[str, VoiceSpec]
    segments: List[Segment]
    cast: Dict[str, str] = field(default_factory=dict)      # role -> character names
    lexicon: Dict[str, str] = field(default_factory=dict)

    @property
    def utterances(self) -> List[Utterance]:
        return [s for s in self.segments if isinstance(s, Utterance)]

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
    file_voices: Dict[str, VoiceSpec] = {}
    lexicon: Dict[str, str] = dict(base_lexicon or {})   # course lexicon; @lexicon entries win
    cast: Dict[str, List[str]] = {}
    raw: List[Tuple[int, str, str, List[str]]] = []  # (line, role, text, modifier tokens)
    pauses: Dict[int, float] = {}
    order: List[Tuple[str, int]] = []
    problems: List[Tuple[str, int, str]] = []

    def err(n: int, msg: str) -> None:
        problems.append((path, n, msg))

    block: Optional[Tuple[str, int]] = None
    last_role: Optional[str] = None
    for n, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if not s:
            continue
        if s.startswith("#"):
            m = _CAST_LINE.match(s)
            if m:
                for entry in m.group(1).split(";"):
                    if "=" in entry:
                        name, role = (x.strip() for x in entry.split("=", 1))
                        cast.setdefault(role, []).append(name)
            continue
        if block:
            if s == "@end":
                block = None
            elif block[0] == "voices":
                _parse_voice_line(s, n, file_voices, err)
            else:
                m = _LEXICON_LINE.match(s)
                if m:
                    lexicon[m.group(1)] = m.group(2)
                else:
                    err(n, f"bad @lexicon line (expected 'word = spoken form'): {s!r}")
            continue
        if s in ("@voices", "@lexicon"):
            block = (s[1:], n)
            continue
        if s.startswith("@"):
            err(n, f"unknown directive {s.split()[0]!r} (known: @voices, @lexicon, @end)")
            continue

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
        if not _ROLE_NAME.match(head):
            err(n, f"malformed tag {head!r}")
            continue
        if not body:
            if head[0].islower():
                err(n, f"unknown control tag [{m.group(1)}] (known: pause)")
            else:
                err(n, f"empty utterance for role {head}")
            continue
        raw.append((n, head, body, tokens[1:]))
        order.append(("u", n))
        last_role = head

    if block:
        err(block[1], f"@{block[0]} block is not closed with @end")

    voices.update(file_voices)
    if adjust_voice is not None:
        voices = {role: adjust_voice(role, spec) for role, spec in voices.items()}
    by_line = {r[0]: r for r in raw}
    segments: List[Segment] = []
    for kind, n in order:
        if kind == "p":
            segments.append(Pause(n, pauses[n]))
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
                  {r: " / ".join(names) for r, names in cast.items()}, lexicon)


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
