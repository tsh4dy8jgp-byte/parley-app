"""Resolve a language code to an Edge TTS voice."""

from __future__ import annotations

from typing import List, Optional

import edge_tts

# Curated defaults for common languages so results are deterministic and
# pleasant out of the box. Anything not listed falls back to a live lookup
# against the full Edge voice catalogue.
PREFERRED_VOICES = {
    "en": "en-US-AriaNeural",
    "en-gb": "en-GB-SoniaNeural",
    "es": "es-ES-ElviraNeural",
    "fr": "fr-FR-DeniseNeural",
    "de": "de-DE-KatjaNeural",
    "it": "it-IT-ElsaNeural",
    "pt": "pt-BR-FranciscaNeural",
    "ru": "ru-RU-SvetlanaNeural",
    "kk": "kk-KZ-AigulNeural",
    "tr": "tr-TR-EmelNeural",
    "ar": "ar-SA-ZariyahNeural",
    "hi": "hi-IN-SwaraNeural",
    "zh": "zh-CN-XiaoxiaoNeural",
    "ja": "ja-JP-NanamiNeural",
    "ko": "ko-KR-SunHiNeural",
    "uk": "uk-UA-PolinaNeural",
    "pl": "pl-PL-ZofiaNeural",
    "nl": "nl-NL-ColetteNeural",
}

# *Multilingual* voices guess the language of every request, so their pronunciation is unreliable
# in any language, worst on short lines. Every place that uses a voice warns about them.
MULTILINGUAL_RISK = ("it guesses the language of each line, so its pronunciation may be messy, "
                     "especially on short lines; a single-language voice is safer")


def is_multilingual(voice: str) -> bool:
    return "Multilingual" in voice


def multilingual_warning(who: str, voice: str) -> str:
    """The warning for *who* (e.g. "role A", "the narrator") using a *Multilingual* voice."""
    return f"warning: {who} uses multilingual voice {voice}; {MULTILINGUAL_RISK}"


async def resolve_voice(language: str, gender: Optional[str] = None) -> str:
    """Return a voice short name for *language* (e.g. ``en``, ``pt-BR``).

    *gender* may be ``"Male"`` or ``"Female"`` to filter the live lookup.
    Raises ``ValueError`` if no voice supports the language.
    """
    key = language.strip().lower()
    if gender is None and key in PREFERRED_VOICES:
        return PREFERRED_VOICES[key]

    manager = await edge_tts.VoicesManager.create()
    if "-" in key:
        locale = "-".join(part.capitalize() if i else part for i, part in enumerate(key.split("-")))
        matches = manager.find(Locale=locale)
    else:
        matches = manager.find(Language=key)

    if gender:
        wanted = gender.strip().capitalize()
        gender_matches = [v for v in matches if v["Gender"] == wanted]
        matches = gender_matches or matches

    if not matches:
        raise ValueError(
            f"No Edge TTS voice found for language {language!r}. "
            "Run `audiobook --list-voices` to see what's available."
        )
    return matches[0]["ShortName"]


async def list_voices(language: Optional[str] = None) -> List[dict]:
    """Return the voice catalogue, optionally filtered by language code."""
    manager = await edge_tts.VoicesManager.create()
    if language:
        key = language.strip().lower()
        if "-" in key:
            locale = "-".join(
                part.capitalize() if i else part for i, part in enumerate(key.split("-"))
            )
            return manager.find(Locale=locale)
        return manager.find(Language=key)
    return manager.voices
