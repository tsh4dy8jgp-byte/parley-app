"""Role -> edge-tts voice mapping: voices.yaml, prosody arithmetic, voice warnings."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional

from ..audiobook.voices import is_multilingual, multilingual_warning
from ..readtext import read_text

RATE_RE = re.compile(r"^[+-]\d{1,3}%$")
VOLUME_RE = RATE_RE
PITCH_RE = re.compile(r"^[+-]\d{1,3}Hz$")
PROSODY_KEYS = ("rate", "pitch", "volume")


@dataclass(frozen=True)
class VoiceSpec:
    """One edge-tts request configuration."""

    voice: str
    rate: str = "+0%"
    pitch: str = "+0Hz"
    volume: str = "+0%"

    def with_prosody(self, **changes: str) -> "VoiceSpec":
        return replace(self, **{k: v for k, v in changes.items() if v is not None})

    def shifted(self, rate: int = 0, pitch: int = 0, volume: int = 0) -> "VoiceSpec":
        """Return a copy with the given deltas added (rate/volume in %, pitch in Hz)."""
        return replace(
            self,
            rate=_fmt(_num(self.rate) + rate, "%"),
            pitch=_fmt(_num(self.pitch) + pitch, "Hz"),
            volume=_fmt(_num(self.volume) + volume, "%"),
        )

    def describe(self) -> str:
        parts = [f"{k}={getattr(self, k)}" for k in PROSODY_KEYS
                 if _num(getattr(self, k)) != 0]
        return " ".join(parts) or "-"


# Roles that frame the lesson rather than act in it: no shadowing gap after them.
NARRATOR_ROLES = frozenset({"Narrator"})


def _num(value: str) -> int:
    return int(re.sub(r"(%|Hz)$", "", value))


def _fmt(n: int, unit: str) -> str:
    return f"{n:+d}{unit}"


def validate_prosody(key: str, value: str) -> Optional[str]:
    """Return an error message, or None if *value* is valid for *key*."""
    if key == "pitch":
        return None if PITCH_RE.match(value) else f"pitch must look like +5Hz / -10Hz, got {value!r}"
    if key in ("rate", "volume"):
        return None if RATE_RE.match(value) else f"{key} must look like +10% / -20%, got {value!r}"
    return f"unknown prosody key {key!r} (use rate, pitch, volume)"


def parse_voice_entry(value) -> VoiceSpec:
    """Build a VoiceSpec from a yaml value: 'de-AT-JonasNeural' or {voice:, rate:, ...}."""
    if isinstance(value, str):
        return VoiceSpec(value)
    if not isinstance(value, Mapping) or "voice" not in value:
        raise ValueError(f"voice entry needs a 'voice' key: {value!r}")
    kwargs = {"voice": str(value["voice"])}
    for key in PROSODY_KEYS:
        if key in value:
            v = str(value[key])
            err = validate_prosody(key, v)
            if err:
                raise ValueError(err)
            kwargs[key] = v
    return VoiceSpec(**kwargs)


def load_voices_yaml(path: Path) -> Dict[str, VoiceSpec]:
    import yaml

    data = yaml.safe_load(read_text(path)) or {}
    if not isinstance(data, Mapping):
        raise ValueError(f"{path}: expected a mapping of role -> voice")
    data = data.get("voices", data)
    return {str(role): parse_voice_entry(v) for role, v in data.items()}


def duplicate_voice_warnings(voices: Mapping[str, VoiceSpec], used_roles: Iterable[str]) -> List[str]:
    """Warn about voice choices that will sound wrong in one file.

    Identical voice+rate+pitch+volume on two roles -> indistinguishable (warning).
    A *Multilingual* voice -> pronunciation may be messy (warning).
    Same base voice with different prosody -> only prosody tells them apart (note).
    """
    used = [r for r in dict.fromkeys(used_roles) if r in voices]
    out: List[str] = [multilingual_warning(f"role {r}", voices[r].voice)
                      for r in used if is_multilingual(voices[r].voice)]
    for i, a in enumerate(used):
        for b in used[i + 1:]:
            va, vb = voices[a], voices[b]
            if va == vb:
                out.append(f"warning: roles {a} and {b} use identical settings ({va.voice} {va.describe()})"
                           " and will sound the same")
            elif va.voice == vb.voice:
                out.append(f"note: roles {a} and {b} share base voice {va.voice}; "
                           f"only prosody differs ({va.describe()} vs {vb.describe()})")
    return out


async def check_voices_exist(voices: Iterable[VoiceSpec]) -> List[str]:
    """Return voice names that the live edge-tts catalogue does not know."""
    import edge_tts

    catalogue = {v["ShortName"] for v in await edge_tts.list_voices()}
    return sorted({v.voice for v in voices} - catalogue)
