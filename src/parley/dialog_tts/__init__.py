"""Multi-voice dialogue rendering with edge-tts."""

from .script import Pause, Script, ScriptError, Utterance, parse_file, parse_text
from .voices import DEFAULT_VOICES, VoiceSpec

__all__ = ["Pause", "Script", "ScriptError", "Utterance", "parse_file", "parse_text",
           "DEFAULT_VOICES", "VoiceSpec"]
