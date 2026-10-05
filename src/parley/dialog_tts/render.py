"""Turn parsed scripts into audio files, subtitles and clip folders."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

from ..mp3tags import Tags, write_tags
from .script import SLOW_DELTA, Pause, Script, Sound
from .sounds import load_sound
from .stitch import (FRAME_RATE, HIFI, Gaps, assemble, clip_ms, decode, finalize, plan_timeline, role_gains,
                     trim_silence)
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
    music_bitrate: str = "128k"     # for files with sound inserts (48 kHz stereo)
    tags: Tags = field(default_factory=Tags)   # mp3 tags; the title defaults to the file name


# Title of each variant: (default, suffix). A title the user typed gets a readable suffix instead.
_TITLE = {"": lambda stem: (stem, ""), ".shadow": lambda stem: (f"{stem}.shadow", " (shadow)"),
          ".slow": lambda stem: (f"{stem}.slow", " (slow)")}


def jobs_for(script: Script, opts: RenderOptions) -> List[Job]:
    jobs = [(u.speak, u.spec) for u in script.utterances]
    if opts.slow:
        jobs += [(u.speak, u.spec.shifted(rate=SLOW_DELTA)) for u in script.utterances]
    return jobs


def render_outputs(script: Script, audio: Dict[Job, bytes], fmt: str, out_dir: Path,
                   opts: RenderOptions) -> List[Tuple[Path, int]]:
    """Write every requested output for *script*; returns [(path, duration_ms)].

    A script with sounds is mixed at 48 kHz stereo (HIFI); its sounds must have been looked up
    with sounds.attach_sounds.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(script.path).name.split(".")[0]          # lektion_02.tagged.txt -> lektion_02
    utts = script.utterances
    roles = [u.role for u in utts]
    rate, channels = HIFI if script.sounds else (FRAME_RATE, 1)
    bitrate = opts.music_bitrate if script.sounds else opts.bitrate

    def clips_for(slow: bool):
        out = []
        for u in utts:
            spec = u.spec.shifted(rate=SLOW_DELTA) if slow else u.spec
            out.append(trim_silence(decode(audio[(u.speak, spec)], fmt, rate, channels)))
        return out

    normal = clips_for(False)
    gains = role_gains(normal, roles, opts.target, opts.loudness)
    normal = [c.apply_gain(gains[r]) for c, r in zip(normal, roles)]

    sounds = _sound_clips(script, rate, channels, opts)

    def in_order(speech):
        """The clips plan_timeline expects: utterances and sounds, in script order."""
        lines = iter(speech)
        return [sounds[(seg.name, seg.opts)] if isinstance(seg, Sound) else next(lines)
                for seg in script.segments if not isinstance(seg, Pause)]

    variants = [("", normal, None)]
    if opts.shadow:
        variants.append((".shadow", normal, opts.shadow_factor))
    if opts.slow:
        slow = [c.apply_gain(gains[r]) for c, r in zip(clips_for(True), roles)]
        variants.append((".slow", slow, None))

    written: List[Tuple[Path, int]] = []
    for suffix, speech, shadow in variants:
        clips = in_order(speech)
        gaps = Gaps(**{**opts.gaps.__dict__, "shadow_factor": shadow})
        tl = plan_timeline(script.segments, [clip_ms(c) for c in clips], gaps)
        mix, _ = finalize(assemble(tl, clips), opts.target, opts.loudness)
        name = f"{stem}{suffix}"

        def out(ext: str, name: str = name) -> Path:  # not with_suffix(): name contains dots
            return out_dir / f"{name}{ext}"

        mix.export(out(".mp3"), format="mp3", bitrate=bitrate)
        write_tags(out(".mp3"), opts.tags.with_title(*_TITLE[suffix](stem)))
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
            c.export(folder / f"{i:03d}_{u.role}.mp3", format="mp3", bitrate=bitrate)
        written.append((folder, 0))
    return written


def _sound_clips(script: Script, rate: int, channels: int, opts: RenderOptions) -> Dict:
    """(name, options) -> the leveled, faded clip; each file is decoded once per set of options."""
    out: Dict = {}
    for s in script.sounds:
        key = (s.name, s.opts)
        if key in out:
            continue
        path = script.sound_files.get(s.name)
        if path is None:
            raise RuntimeError(f"line {s.line}: sound {s.name} has no file (look it up with attach_sounds)")
        try:
            out[key] = load_sound(path, s.opts, rate, channels, opts.target, opts.loudness)
        except RuntimeError as e:
            raise RuntimeError(f"line {s.line}: sound {s.name}: {e}") from e
    return out


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
