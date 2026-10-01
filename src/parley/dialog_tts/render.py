"""Turn parsed scripts into audio files, subtitles and clip folders."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

from .script import SLOW_DELTA, Script
from .stitch import Gaps, assemble, clip_ms, decode, finalize, plan_timeline, role_gains, trim_silence
from .subtitles import to_lrc, to_srt
from .tts import Backend, synth_all
from .voices import VoiceSpec

Job = Tuple[str, VoiceSpec]


@dataclass
class RenderOptions:
    gaps: Gaps = field(default_factory=Gaps)
    shadow_factor: float = 1.3
    shadow: bool = False
    slow: bool = False
    srt: bool = False
    lrc: bool = False
    clips: bool = False
    wav: bool = False
    loudness: str = "lufs"          # "lufs" or "dbfs"
    target: float = -16.0           # LUFS target (or dBFS target in dbfs mode)
    bitrate: str = "64k"
    album: str = "Parley"           # mp3 album tag


def jobs_for(script: Script, opts: RenderOptions) -> List[Job]:
    jobs = [(u.speak, u.spec) for u in script.utterances]
    if opts.slow:
        jobs += [(u.speak, u.spec.shifted(rate=SLOW_DELTA)) for u in script.utterances]
    return jobs


def render_outputs(script: Script, audio: Dict[Job, bytes], fmt: str, out_dir: Path,
                   opts: RenderOptions) -> List[Tuple[Path, int]]:
    """Write every requested output for *script*; returns [(path, duration_ms)]."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(script.path).name.split(".")[0]          # lektion_02.tagged.txt -> lektion_02
    utts = script.utterances
    roles = [u.role for u in utts]

    def clips_for(slow: bool):
        out = []
        for u in utts:
            spec = u.spec.shifted(rate=SLOW_DELTA) if slow else u.spec
            out.append(trim_silence(decode(audio[(u.speak, spec)], fmt)))
        return out

    normal = clips_for(False)
    gains = role_gains(normal, roles, opts.target, opts.loudness)
    normal = [c.apply_gain(gains[r]) for c, r in zip(normal, roles)]

    variants = [("", normal, None)]
    if opts.shadow:
        variants.append((".shadow", normal, opts.shadow_factor))
    if opts.slow:
        slow = [c.apply_gain(gains[r]) for c, r in zip(clips_for(True), roles)]
        variants.append((".slow", slow, None))

    written: List[Tuple[Path, int]] = []
    for suffix, clips, shadow in variants:
        gaps = Gaps(**{**opts.gaps.__dict__, "shadow_factor": shadow})
        tl = plan_timeline(script.segments, [clip_ms(c) for c in clips], gaps)
        mix, _ = finalize(assemble(tl, clips), opts.target, opts.loudness)
        name = f"{stem}{suffix}"

        def out(ext: str, name: str = name) -> Path:  # not with_suffix(): name contains dots
            return out_dir / f"{name}{ext}"

        mix.export(out(".mp3"), format="mp3", bitrate=opts.bitrate,
                   tags={"title": name, "album": opts.album})
        written.append((out(".mp3"), tl.total_ms))
        if opts.wav:
            mix.export(out(".wav"), format="wav")
            written.append((out(".wav"), tl.total_ms))
        if opts.srt:
            out(".srt").write_text(to_srt(tl.cues, script.label), encoding="utf-8")
            written.append((out(".srt"), tl.total_ms))
        if opts.lrc:
            out(".lrc").write_text(to_lrc(tl.cues, script.label, name), encoding="utf-8")
            written.append((out(".lrc"), tl.total_ms))

    if opts.clips:
        folder = out_dir / f"{stem}_clips"
        folder.mkdir(exist_ok=True)
        for i, (u, c) in enumerate(zip(utts, normal), 1):
            c.export(folder / f"{i:03d}_{u.role}.mp3", format="mp3", bitrate=opts.bitrate)
        written.append((folder, 0))
    return written


async def render_scripts(scripts: Sequence[Script], backend: Backend, out_dir: Path,
                         opts: RenderOptions, on_progress=None) -> List[Tuple[Path, int]]:
    """Synthesize all lines of all scripts in one concurrent batch, then build the outputs."""
    jobs: List[Job] = []
    for s in scripts:
        jobs += jobs_for(s, opts)
    audio = await synth_all(backend, jobs, on_progress)
    written: List[Tuple[Path, int]] = []
    for s in scripts:
        written += render_outputs(s, audio, backend.fmt, out_dir, opts)
    return written
