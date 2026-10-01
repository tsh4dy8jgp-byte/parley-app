"""SRT and LRC subtitles from a planned timeline."""

from __future__ import annotations

from typing import Callable, List

from .stitch import Cue


def _srt_time(ms: int) -> str:
    h, rest = divmod(ms, 3_600_000)
    m, rest = divmod(rest, 60_000)
    s, ms = divmod(rest, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _lrc_time(ms: int) -> str:
    m, rest = divmod(ms, 60_000)
    return f"{m:02d}:{rest / 1000:05.2f}"


def to_srt(cues: List[Cue], label: Callable[[str], str]) -> str:
    blocks = []
    for i, cue in enumerate(cues, 1):
        u = cue.utterance
        blocks.append(f"{i}\n{_srt_time(cue.start_ms)} --> {_srt_time(cue.end_ms)}\n"
                      f"{label(u.role)}: {u.text}\n")
    return "\n".join(blocks)


def to_lrc(cues: List[Cue], label: Callable[[str], str], title: str = "") -> str:
    lines = [f"[ti:{title}]"] if title else []
    lines += [f"[{_lrc_time(c.start_ms)}]{label(c.utterance.role)}: {c.utterance.text}" for c in cues]
    return "\n".join(lines) + "\n"
