"""The edge-tts voice catalogue: languages, voices per locale, narrator choice and automatic casting."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .audiobook.voices import PREFERRED_VOICES
from .dialog_tts.voices import DEFAULT_VOICES, VoiceSpec

# When a language has fewer voices than the dialogue has speakers, voices are reused with one of
# these (rate %, pitch Hz) shifts so every speaker still sounds different (cf. Female4 / Child1).
REUSE_SHIFTS: Tuple[Tuple[int, int], ...] = ((5, 15), (-5, -12), (10, 30), (-10, -25), (0, 45))


@dataclass(frozen=True)
class Voice:
    name: str        # edge-tts ShortName, e.g. en-US-AvaNeural
    gender: str      # Female | Male | "" (unknown)
    locale: str      # en-US
    language: str    # English (United States)

    @property
    def short(self) -> str:
        """Ava for en-US-AvaNeural."""
        s = self.name[len(self.locale) + 1:] if self.name.startswith(self.locale + "-") else self.name
        return s[:-len("Neural")] if s.endswith("Neural") else s

    @property
    def multilingual(self) -> bool:
        return "Multilingual" in self.name

    @property
    def label(self) -> str:
        return f"{self.short} · {self.gender[:1]}" if self.gender else self.short


class Catalog:
    def __init__(self, voices: Iterable[Voice], offline: bool = False):
        self.voices: List[Voice] = list(voices)
        self.offline = offline
        self._by_name = {v.name: v for v in self.voices}
        self._langs: Dict[str, str] = {}
        for v in self.voices:
            self._langs.setdefault(v.locale, v.language)

    @classmethod
    def from_edge(cls, raw: Sequence[Mapping], offline: bool = False) -> "Catalog":
        voices = []
        for r in raw:
            name, locale = r.get("ShortName"), r.get("Locale")
            if not name or not locale:
                continue
            language = r.get("LocaleName") or str(r.get("FriendlyName", "")).rpartition(" - ")[2] or locale
            voices.append(Voice(name, r.get("Gender", ""), locale, language))
        return cls(voices, offline)

    def languages(self) -> List[Tuple[str, str]]:
        """[(label, locale)] sorted by label; labels are made unique by appending the locale."""
        counts: Dict[str, int] = {}
        for label in self._langs.values():
            counts[label] = counts.get(label, 0) + 1
        out = [(label if counts[label] == 1 else f"{label} · {loc}", loc) for loc, label in self._langs.items()]
        return sorted(out, key=lambda x: x[0].casefold())

    def language_label(self, locale: str) -> str:
        return dict((loc, label) for label, loc in self.languages()).get(locale, locale)

    def get(self, name: str) -> Optional[Voice]:
        return self._by_name.get(name)

    def voices_for(self, locale: str) -> List[Voice]:
        """Voices of *locale* in catalogue order; *Multilingual* ones last (they guess the language)."""
        vs = [v for v in self.voices if v.locale == locale]
        if not vs:  # e.g. "de" or a locale the catalogue does not have: use the language
            lang = locale.split("-")[0].lower()
            vs = [v for v in self.voices if v.locale.split("-")[0].lower() == lang]
        return sorted(vs, key=lambda v: v.multilingual)

    def pick_narrator(self, locale: str, gender: Optional[str] = None) -> str:
        """A voice for single-voice narration; the curated audiobook default when it fits."""
        vs = self.voices_for(locale)
        if not vs:
            raise ValueError(f"no voice found for language {locale!r}")
        if gender in ("Female", "Male"):
            vs = [v for v in vs if v.gender == gender] or vs
        names = [v.name for v in vs]
        for key in (locale.lower(), locale.split("-")[0].lower()):
            if PREFERRED_VOICES.get(key) in names:
                return PREFERRED_VOICES[key]
        return names[0]

    def auto_cast(self, locale: str, roles: Sequence[str]) -> Dict[str, VoiceSpec]:
        """Give each role a voice: alternating female/male, all different; reuse with a shift if short.

        Roles named Female*/Male* get that gender. Returns {} when the language has no voices.
        """
        pool = self.voices_for(locale)
        plain = [v for v in pool if not v.multilingual]
        pool = plain or pool
        if not pool:
            return {}
        preferred = PREFERRED_VOICES.get(locale.lower()) or PREFERRED_VOICES.get(locale.split("-")[0].lower())
        pool.sort(key=lambda v: v.name != preferred)
        by_gender = {g: [v for v in pool if v.gender == g] for g in ("Female", "Male")}
        used: Dict[str, int] = {}
        cast: Dict[str, VoiceSpec] = {}
        for i, role in enumerate(roles):
            want = _gender_hint(role) or ("Female" if i % 2 == 0 else "Male")
            candidates = by_gender[want] or pool
            v = min(candidates, key=lambda v: used.get(v.name, 0))  # least used, catalogue order on ties
            n = used.get(v.name, 0)
            used[v.name] = n + 1
            spec = VoiceSpec(v.name)
            if n:
                rate, pitch = REUSE_SHIFTS[(n - 1) % len(REUSE_SHIFTS)]
                spec = spec.shifted(rate=rate, pitch=pitch)
            cast[role] = spec
        return cast


def _gender_hint(role: str) -> Optional[str]:
    r = role.lower()
    if r.startswith("female"):
        return "Female"
    if r.startswith("male"):
        return "Male"
    return None


def fallback() -> Catalog:
    """A small offline catalogue: the curated audiobook voices plus the German course cast."""
    voices: Dict[str, Voice] = {}
    for name in PREFERRED_VOICES.values():
        voices[name] = Voice(name, "Female", name.rsplit("-", 1)[0], name.rsplit("-", 1)[0])
    for role, spec in DEFAULT_VOICES.items():
        gender = "Female" if role.startswith(("Female", "Child")) else "Male"
        voices.setdefault(spec.voice, Voice(spec.voice, gender, spec.voice.rsplit("-", 1)[0],
                                            spec.voice.rsplit("-", 1)[0]))
    return Catalog(voices.values(), offline=True)


def load_cached(path: Path) -> Catalog:
    """The catalogue saved by the last online fetch, else the built-in fallback."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(raw, list) and raw:
            return Catalog.from_edge(raw)
    except (OSError, ValueError):
        pass
    return fallback()


async def fetch(cache_file: Path) -> Catalog:
    """Download the live catalogue and remember it for offline starts."""
    import edge_tts

    raw = await edge_tts.list_voices()
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=cache_file.parent, suffix=".part")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False)
        os.replace(tmp, cache_file)
    except OSError:
        pass
    return Catalog.from_edge(raw)
