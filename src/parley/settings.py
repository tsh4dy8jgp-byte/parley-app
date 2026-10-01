"""User settings: one dataclass, persisted as JSON. Bad or unknown values fall back to defaults."""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Dict, Optional, Union

from .audiobook.chunker import DEFAULT_CHUNK_SIZE
from .dialog_tts.voices import VoiceSpec

CONFIG_DIR = Path(os.environ.get("PARLEY_HOME") or Path.home() / ".config" / "parley")
CACHE_DIR = Path.home() / ".cache" / "parley"
SETTINGS_FILE = CONFIG_DIR / "settings.json"
DRAFT_FILE = CONFIG_DIR / "draft.txt"
ROLE_NAME = re.compile(r"^[A-Za-z_]\w*$")   # same rule as the dialogue parser

# Folders used while the app was called TTS Studio; `migrate_legacy` moves them once.
LEGACY_CONFIG_DIR = Path.home() / ".config" / "tts-studio"
LEGACY_CACHE_DIR = Path.home() / ".cache" / "tts-studio"


@dataclass
class Speaker:
    """A voice choice: an explicit voice ("" = automatic) plus prosody deltas."""

    voice: str = ""
    rate: int = 0       # %
    pitch: int = 0      # Hz
    volume: int = 0     # %

    def spec(self, auto: Optional[VoiceSpec] = None) -> VoiceSpec:
        """The explicit voice, or *auto* when none is set, with this speaker's deltas added."""
        base = VoiceSpec(self.voice) if self.voice else auto
        if base is None:
            raise ValueError("no voice selected")
        return base.shifted(self.rate, self.pitch, self.volume)

    @property
    def is_default(self) -> bool:
        return self == Speaker()


@dataclass
class Settings:
    # Standard tab
    language: str = "en-US"
    gender: str = "Auto"                 # narrator preference: Auto | Female | Male
    out_dir: str = str(Path.home() / "Parley")
    out_name: str = "untitled"
    # Voices
    narrator: Speaker = field(default_factory=Speaker)
    speakers: Dict[str, Speaker] = field(default_factory=dict)
    # Dialogue timing (ms)
    gap_change: int = 450
    gap_same: int = 250
    gap_repeat: int = 700
    # Extra outputs (dialogue only)
    srt: bool = False
    lrc: bool = False
    shadow: bool = False
    shadow_factor: float = 1.3
    slow: bool = False
    clips: bool = False
    wav: bool = False
    # Sound
    loudness: str = "lufs"               # lufs | dbfs
    target: float = -16.0
    # Pronunciation & parsing
    lexicon: str = ""
    continue_speaker: bool = False
    # Performance
    concurrency: int = 4
    chunk_size: int = DEFAULT_CHUNK_SIZE
    cache_dir: str = str(CACHE_DIR)
    # Window
    appearance: str = "System"           # System | Light | Dark
    guide_open: bool = True


CHOICES = {"gender": ("Auto", "Female", "Male"), "loudness": ("lufs", "dbfs"),
           "appearance": ("System", "Light", "Dark")}


def _speaker(data) -> Optional[Speaker]:
    if not isinstance(data, dict):
        return None
    return _coerce(Speaker, data)


def _coerce(cls, data: dict):
    """Build *cls* from *data*, keeping only values whose type matches the field default."""
    default = cls()
    kwargs = {}
    for f in fields(cls):
        if f.name not in data:
            continue
        value, current = data[f.name], getattr(default, f.name)
        if f.name == "narrator":
            value = _speaker(value)
        elif f.name == "speakers":
            value = ({k: sp for k, v in value.items()
                      if ROLE_NAME.match(str(k)) and (sp := _speaker(v)) is not None}
                     if isinstance(value, dict) else None)
        elif isinstance(current, bool):
            value = value if isinstance(value, bool) else None
        elif isinstance(current, float):
            value = float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None
        elif isinstance(current, int):
            value = value if isinstance(value, int) and not isinstance(value, bool) else None
        elif isinstance(current, str):
            value = value if isinstance(value, str) else None
        if value is not None and value not in CHOICES.get(f.name, (value,)):
            value = None
        if value is not None:
            kwargs[f.name] = value
    return cls(**kwargs)


def from_dict(data) -> Settings:
    return _coerce(Settings, data) if isinstance(data, dict) else Settings()


def load(path: Union[str, Path, None] = None) -> Settings:
    try:
        return from_dict(json.loads(Path(path or SETTINGS_FILE).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return Settings()


def save(settings: Settings, path: Union[str, Path, None] = None) -> None:
    path = Path(path or SETTINGS_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(settings), indent=2, ensure_ascii=False), encoding="utf-8")


def load_draft(path: Union[str, Path, None] = None) -> str:
    try:
        return Path(path or DRAFT_FILE).read_text(encoding="utf-8")
    except OSError:
        return ""


def save_draft(text: str, path: Union[str, Path, None] = None) -> None:
    path = Path(path or DRAFT_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def migrate_legacy(config_dir: Path = CONFIG_DIR, cache_dir: Path = CACHE_DIR,
                   old_config: Path = LEGACY_CONFIG_DIR, old_cache: Path = LEGACY_CACHE_DIR) -> None:
    """Move TTS Studio's settings, draft and cache to Parley's folders, once.

    An old folder is moved only while the new one does not exist. A saved cache folder that
    pointed at the old cache is updated; a saved output folder is kept, the user's files are there.
    """
    _move_dir(old_config, config_dir)
    if not _move_dir(old_cache, cache_dir):
        return
    path = config_dir / "settings.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("cache_dir") == str(old_cache):
            data["cache_dir"] = str(cache_dir)
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except (OSError, ValueError):
        pass   # the cache is only a cache: at worst lines are synthesised again


def _move_dir(src: Path, dst: Path) -> bool:
    """Move folder *src* to *dst* unless *dst* already exists; True if it was moved."""
    if dst.exists() or not src.is_dir():
        return False
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
    except OSError:
        return False
    return True
