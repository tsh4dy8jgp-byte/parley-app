"""Loudness measurement with ffmpeg's EBU R128 filter."""

from __future__ import annotations

import io
import re
import shutil
import subprocess
from typing import Optional

_INTEGRATED = re.compile(r"I:\s+(-?\d+(?:\.\d+)?)\s+LUFS")


def ffmpeg_path() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError("ffmpeg not found on PATH (install it: brew/apt/winget install ffmpeg)")
    return exe


def measure_lufs(seg) -> Optional[float]:
    """Integrated loudness (LUFS) of a pydub AudioSegment, or None if it cannot be measured.

    R128 needs 400 ms blocks, so short audio (a one-word line) is looped to 1 s first;
    looping does not change its loudness, and every role is measured the same way.
    Stereo is measured on its mono downmix, so music compares like for like with the mono speech
    and a stereo file comes out as loud as a speech-only one.
    """
    if len(seg) == 0:
        return None
    if seg.channels > 1:
        seg = seg.set_channels(1)
    if len(seg) < 1000:
        seg = seg * (1000 // len(seg) + 1)
    buf = io.BytesIO()
    seg.export(buf, format="wav")
    proc = subprocess.run(
        [ffmpeg_path(), "-hide_banner", "-nostats", "-f", "wav", "-i", "pipe:0",
         "-af", "ebur128=framelog=quiet", "-f", "null", "-"],
        input=buf.getvalue(), capture_output=True, check=False,
    )
    found = _INTEGRATED.findall(proc.stderr.decode("utf-8", "replace"))
    if not found:
        return None
    value = float(found[-1])
    return None if value <= -69 else value
