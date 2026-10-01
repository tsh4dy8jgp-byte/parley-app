"""Timeline planning (pure) and audio assembly (pydub)."""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Sequence, Tuple

from .script import Pause, Segment, Utterance
from .voices import NARRATOR_ROLES

FRAME_RATE = 24000  # edge-tts native rate


@dataclass(frozen=True)
class Gaps:
    change_ms: int = 450            # between two lines of different speakers
    same_ms: int = 250              # between two lines of the same speaker
    repeat_ms: int = 700            # between repetitions of one line ([Role repeat=N])
    lead_ms: int = 300              # silence at the start of the file
    tail_ms: int = 800              # silence at the end of the file
    shadow_factor: Optional[float] = None   # e.g. 1.3 -> silence of 1.3 x clip after each line
    no_shadow_roles: FrozenSet[str] = NARRATOR_ROLES


@dataclass(frozen=True)
class Placed:
    start_ms: int
    dur_ms: int
    utt_index: Optional[int] = None   # index into the utterance list, None = silence
    why: str = ""                     # lead, gap-change, gap-same, pause, repeat, shadow, tail


@dataclass(frozen=True)
class Cue:
    start_ms: int
    end_ms: int
    utterance: Utterance


@dataclass
class Timeline:
    items: List[Placed] = field(default_factory=list)
    cues: List[Cue] = field(default_factory=list)
    total_ms: int = 0


def plan_timeline(segments: Sequence[Segment], durations_ms: Sequence[int], gaps: Gaps) -> Timeline:
    """Lay out *segments* in time. durations_ms[i] is the length of the i-th utterance clip."""
    tl = Timeline()
    pos = 0

    def put(dur: int, why: str, idx: Optional[int] = None) -> None:
        nonlocal pos
        if dur > 0 or idx is not None:
            tl.items.append(Placed(pos, dur, idx, why))
            pos += dur

    put(gaps.lead_ms, "lead")
    prev_role: Optional[str] = None     # role of the previous item if it was an utterance
    u = 0
    for seg in segments:
        if isinstance(seg, Pause):
            put(round(seg.seconds * 1000), "pause")
            prev_role = None
            continue
        if prev_role is not None:
            same = seg.role == prev_role
            put(gaps.same_ms if same else gaps.change_ms, "gap-same" if same else "gap-change")
        dur = durations_ms[u]
        start = pos
        for rep in range(seg.repeat):
            if rep:
                put(gaps.repeat_ms, "repeat")
            put(dur, "clip", u)
        tl.cues.append(Cue(start, pos, seg))
        if gaps.shadow_factor and seg.role not in gaps.no_shadow_roles:
            put(round(dur * gaps.shadow_factor), "shadow")
        prev_role = seg.role
        u += 1
    put(gaps.tail_ms, "tail")
    tl.total_ms = pos
    return tl


# ---------------------------------------------------------------- audio side

def decode(data: bytes, fmt: str):
    from pydub import AudioSegment

    seg = AudioSegment.from_file(io.BytesIO(data), format=fmt)
    return seg.set_frame_rate(FRAME_RATE).set_channels(1).set_sample_width(2)


def trim_silence(seg, threshold_dbfs: float = -45.0, keep_ms: int = 40):
    """Remove leading/trailing silence edge-tts pads each clip with, keeping *keep_ms*."""
    from pydub.silence import detect_leading_silence

    if len(seg) == 0:
        return seg
    lead = detect_leading_silence(seg, silence_threshold=threshold_dbfs, chunk_size=5)
    tail = detect_leading_silence(seg.reverse(), silence_threshold=threshold_dbfs, chunk_size=5)
    if lead + tail >= len(seg):
        return seg
    return seg[max(0, lead - keep_ms): len(seg) - max(0, tail - keep_ms)]


def assemble(tl: Timeline, clips: Sequence) -> "AudioSegment":  # noqa: F821
    """Render the timeline to one AudioSegment (joined as raw PCM for exact timing)."""
    from pydub import AudioSegment

    bytes_per_ms = FRAME_RATE * 2 // 1000
    parts: List[bytes] = []
    for item in tl.items:
        if item.utt_index is None:
            parts.append(b"\x00" * (item.dur_ms * bytes_per_ms))
        else:
            raw = clips[item.utt_index].raw_data
            want = item.dur_ms * bytes_per_ms   # clip lengths are rounded to whole ms
            parts.append(raw[:want].ljust(want, b"\x00"))
    return AudioSegment(data=b"".join(parts), sample_width=2, frame_rate=FRAME_RATE, channels=1)


def clip_ms(seg) -> int:
    return int(len(seg.raw_data) // (FRAME_RATE * 2 // 1000))


def role_gains(clips: Sequence, roles: Sequence[str], target: float, mode: str = "lufs") -> Dict[str, float]:
    """One gain (dB) per role so every role reaches *target* (LUFS or dBFS)."""
    from .loudness import measure_lufs

    from pydub import AudioSegment

    gains: Dict[str, float] = {}
    for role in dict.fromkeys(roles):
        joined = AudioSegment.empty()
        for c, r in zip(clips, roles):
            if r == role:
                joined += c
        level = measure_lufs(joined) if mode == "lufs" else None
        if level is None:   # dbfs mode, or too short for a gated LUFS reading
            level = joined.dBFS + (3.0 if mode == "lufs" else 0.0)  # speech: LUFS ~ dBFS + 3
        gains[role] = target - level
    return gains


def finalize(audio, target: float, mode: str = "lufs", peak_ceiling_dbfs: float = -1.0) -> Tuple["AudioSegment", float]:  # noqa: F821
    """Apply one linear gain so the whole file hits *target*, without exceeding the peak ceiling."""
    from .loudness import measure_lufs

    level = measure_lufs(audio) if mode == "lufs" else None
    if level is None:
        level = audio.dBFS
    gain = target - level
    if audio.max_dBFS + gain > peak_ceiling_dbfs:
        gain = peak_ceiling_dbfs - audio.max_dBFS
    return audio.apply_gain(gain), gain
