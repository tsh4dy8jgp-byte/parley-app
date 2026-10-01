"""Multi-voice dialogue rendering with edge-tts."""

from .script import Pause, Script, ScriptError, Utterance, parse_file, parse_text
from .voices import VoiceSpec

__all__ = ["Pause", "Script", "ScriptError", "Utterance", "parse_file", "parse_text", "VoiceSpec"]
