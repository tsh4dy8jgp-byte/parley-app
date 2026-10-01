"""Set-up for the packaged app (PyInstaller build): bundled ffmpeg, no console windows, a self-test.

Only the standard library is imported here: `prepare()` must run before pydub is imported, because
pydub looks ffmpeg up on PATH and binds `subprocess.Popen` at import time.
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, List, Optional, Tuple

CREATE_NO_WINDOW = 0x08000000   # subprocess.CREATE_NO_WINDOW, which only exists on Windows


class _NoWindowPopen(subprocess.Popen):
    """Popen that never opens a console: a windowed .exe would flash one for every ffmpeg call."""

    def __init__(self, *args, **kwargs):
        kwargs["creationflags"] = (kwargs.get("creationflags") or 0) | CREATE_NO_WINDOW
        super().__init__(*args, **kwargs)


def bundle_dir() -> Optional[Path]:
    """Folder the packaged app runs from, or None when running from source."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return None


def prepare() -> None:
    """Put the bundled ffmpeg first on PATH and, on Windows, hide the consoles of child processes."""
    root = bundle_dir()
    if root is None:
        return
    ffmpeg_dir = str(root / "ffmpeg")
    path = os.environ.get("PATH", "")
    if os.path.isdir(ffmpeg_dir) and path.split(os.pathsep)[0] != ffmpeg_dir:
        os.environ["PATH"] = ffmpeg_dir + os.pathsep + path if path else ffmpeg_dir
    if sys.platform == "win32":
        subprocess.Popen = _NoWindowPopen


# --- self-test: `Parley --self-test REPORT [--online]` checks a build without opening the window

def _check_ffmpeg() -> str:
    from . import system

    hint = system.dialogue_ready()
    if hint:
        raise RuntimeError(hint)
    found = [shutil.which(name) for name in ("ffmpeg", "ffprobe")]
    if not all(found):
        raise RuntimeError(f"not on PATH: ffmpeg={found[0]}, ffprobe={found[1]}")
    root = bundle_dir()
    if root is not None and not all(Path(p).resolve().is_relative_to(root.resolve()) for p in found):
        raise RuntimeError(f"not the bundled ffmpeg: {found}")
    return found[0]


def _check_audio() -> str:
    """MP3 encode (ffmpeg + lame), decode (ffprobe + ffmpeg) and loudness (ebur128): all a render uses."""
    from pydub.generators import Sine

    from .dialog_tts.loudness import measure_lufs
    from .dialog_tts.stitch import decode

    buf = io.BytesIO()
    Sine(440).to_audio_segment(duration=800, volume=-12).export(buf, format="mp3")
    seg = decode(buf.getvalue(), "mp3")
    lufs = measure_lufs(seg)
    if len(seg) < 700 or lufs is None:
        raise RuntimeError(f"decoded {len(seg)} ms, loudness {lufs}")
    return f"{len(seg)} ms mp3 round trip, {lufs:.1f} LUFS"


def _check_tts() -> str:
    import certifi
    import edge_tts

    if not Path(certifi.where()).is_file():
        raise RuntimeError(f"no CA bundle at {certifi.where()}")
    return f"edge-tts {edge_tts.__version__}"


def _check_window() -> str:
    import tkinter as tk

    from tkinterdnd2 import TkinterDnD

    from . import app   # every GUI module imports cleanly

    if not app.ICON.is_file():
        raise RuntimeError(f"no window icon at {app.ICON}")
    root = tk.Tk()
    try:
        root.withdraw()
        dnd = TkinterDnD._require(root)
        return f"Tk {root.tk.call('info', 'patchlevel')}, tkdnd {dnd}"
    finally:
        root.destroy()


def _check_speech() -> str:
    """One real edge-tts request (network): TLS, aiohttp and the service, as the app uses them."""
    import asyncio

    from .dialog_tts.stitch import decode
    from .dialog_tts.tts import EdgeBackend
    from .dialog_tts.voices import VoiceSpec

    mp3 = asyncio.run(EdgeBackend(retries=2).synth("Hello from Parley.", VoiceSpec("en-US-AvaNeural")))
    return f"{len(decode(mp3, 'mp3'))} ms from en-US-AvaNeural"


CHECKS: List[Tuple[str, Callable[[], str]]] = [
    ("ffmpeg", _check_ffmpeg),
    ("audio", _check_audio),
    ("tts", _check_tts),
    ("window", _check_window),
]
ONLINE_CHECKS: List[Tuple[str, Callable[[], str]]] = [("speech", _check_speech)]


def self_test(report: Path, online: bool = False) -> int:
    """Run CHECKS (+ ONLINE_CHECKS), write one line per check to *report*; 0 if all pass, else 1."""
    checks = CHECKS + (ONLINE_CHECKS if online else [])
    lines, failed = [], 0
    for name, check in checks:
        try:
            lines.append(f"ok    {name}: {check()}")
        except Exception as e:   # report every failure, not just the first
            failed += 1
            lines.append(f"FAIL  {name}: {type(e).__name__}: {e}")
    lines.append(f"{failed} of {len(checks)} checks failed" if failed else "PASS")
    Path(report).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 1 if failed else 0
