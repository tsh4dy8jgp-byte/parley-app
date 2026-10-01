"""Operating-system helpers: open files and folders, play audio, check ffmpeg, manage the cache."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

_CACHE_FILE = re.compile(r"^[0-9a-f]{64}\.(mp3|wav)$")   # dialog_tts.tts.cache_key names only


def open_path(path: Path) -> None:
    """Open a file or folder with the system's default application."""
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    elif os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", str(path)])


def reveal(path: Path) -> None:
    """Show *path* selected in Finder / Explorer (other systems: open its folder)."""
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)])
    elif os.name == "nt":
        subprocess.Popen(["explorer", "/select,", str(path)])
    else:
        open_path(path if path.is_dir() else path.parent)


def play_bytes(audio: bytes, suffix: str = ".mp3") -> Optional[subprocess.Popen]:
    """Play audio without opening a window when possible; returns the player process."""
    fd, tmp = tempfile.mkstemp(prefix="parley-", suffix=suffix)
    with os.fdopen(fd, "wb") as f:
        f.write(audio)
    if sys.platform == "darwin":
        return subprocess.Popen(["afplay", tmp])
    if shutil.which("ffplay"):
        return subprocess.Popen(["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", tmp])
    open_path(Path(tmp))
    return None


def stop(process: Optional[subprocess.Popen]) -> None:
    if process is not None and process.poll() is None:
        process.terminate()


def dialogue_ready() -> Optional[str]:
    """None if dialogue rendering can run, else a short hint on what to install."""
    try:
        import pydub  # noqa: F401
    except ImportError:
        return 'Dialogues need pydub: pip install -e ".[gui]"'
    if not (shutil.which("ffmpeg") or shutil.which("avconv")):
        hint = {"darwin": "brew install ffmpeg", "win32": "winget install Gyan.FFmpeg"}.get(
            sys.platform, "sudo apt install ffmpeg")
        return f"Dialogues need ffmpeg: {hint}"
    return None


def cache_size(cache_dir: Path) -> int:
    """Bytes used by synthesised lines in *cache_dir*."""
    try:
        return sum(p.stat().st_size for p in Path(cache_dir).expanduser().iterdir()
                   if _CACHE_FILE.match(p.name))
    except OSError:
        return 0


def clear_cache(cache_dir: Path) -> int:
    """Delete synthesised lines (only files named like cache entries); returns how many."""
    n = 0
    try:
        for p in Path(cache_dir).expanduser().iterdir():
            if _CACHE_FILE.match(p.name):
                p.unlink()
                n += 1
    except OSError:
        pass
    return n


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"
