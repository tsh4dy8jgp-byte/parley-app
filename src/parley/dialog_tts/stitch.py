"""Timeline planning (pure) and audio assembly (pydub)."""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Sequence, Tuple, Union

from .script import Pause, Segment, Sound, Utterance
from .voices import NARRATOR_ROLES

FRAME_RATE = 24000  # edge-tts native rate
HIFI = (48000, 2)   # (rate, channels) of a file with sound inserts; 48 kHz keeps whole bytes per ms


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
    clip_index: Optional[int] = None  # index into the clips (utterances and sounds in order), None = silence
    why: str = ""                     # lead, gap-change, gap-same, pause, clip, sound, repeat, shadow, tail


@dataclass(frozen=True)
class Cue:
    start_ms: int
    end_ms: int
    segment: Union[Utterance, Sound]


@dataclass
class Timeline:
    items: List[Placed] = field(default_factory=list)
    cues: List[Cue] = field(default_factory=list)
    total_ms: int = 0


def plan_timeline(segments: Sequence[Segment], durations_ms: Sequence[int], gaps: Gaps) -> Timeline:
    """Lay out *segments* in time. durations_ms[i] is the length of the i-th clip: the utterances and
    sounds of *segments*, in order. A sound gets the speaker-change gap on both sides."""
    tl = Timeline()
    pos = 0

    def put(dur: int, why: str, idx: Optional[int] = None) -> None:
        nonlocal pos
        if dur > 0 or idx is not None:
            tl.items.append(Placed(pos, dur, idx, why))
            pos += dur

    put(gaps.lead_ms, "lead")
    prev_role: Optional[str] = None     # role of the previous utterance, a marker after a sound, None after a pause
    u = 0
    for seg in segments:
        if isinstance(seg, Pause):
            put(round(seg.seconds * 1000), "pause")
            prev_role = None
            continue
        if isinstance(seg, Sound):
            if prev_role is not None:
                put(gaps.change_ms, "gap-change")
            start = pos
            put(durations_ms[u], "sound", u)
            tl.cues.append(Cue(start, pos, seg))
            prev_role = f"\0sound{u}"          # equal to no role, so the next item gets a change gap
            u += 1
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

def decode(data: bytes, fmt: str, rate: int = FRAME_RATE, channels: int = 1):
    """Decode a TTS clip to 16-bit PCM at *rate* / *channels* (ffmpeg resamples where it decodes)."""
    from pydub import AudioSegment

    seg = AudioSegment.from_file(io.BytesIO(data), format=fmt, parameters=["-ar", str(rate), "-ac", str(channels)])
    return seg.set_frame_rate(rate).set_channels(channels).set_sample_width(2)


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
    """Render the timeline to one AudioSegment (joined as raw PCM for exact timing).

    Every clip must have the same format (16-bit; 24 kHz mono, or HIFI when the script has sounds).
    """
    from pydub import AudioSegment

    rate, channels = (clips[0].frame_rate, clips[0].channels) if clips else (FRAME_RATE, 1)
    bytes_per_ms = rate * 2 * channels // 1000
    parts: List[bytes] = []
    for item in tl.items:
        if item.clip_index is None:
            parts.append(b"\x00" * (item.dur_ms * bytes_per_ms))
        else:
            raw = clips[item.clip_index].raw_data
            want = item.dur_ms * bytes_per_ms   # clip lengths are rounded to whole ms
            parts.append(raw[:want].ljust(want, b"\x00"))
    return AudioSegment(data=b"".join(parts), sample_width=2, frame_rate=rate, channels=channels)


def clip_ms(seg) -> int:
    return int(len(seg.raw_data) // (seg.frame_rate * seg.sample_width * seg.channels // 1000))


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
