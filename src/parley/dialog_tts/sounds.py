"""Audio files inserted with [sound Name]: find them in the sounds folder, check them, load them.

[sound Anthem1] plays sounds/Anthem1.mp3 (or .wav, .m4a, ...) next to the tagged file, unless the
@sounds block names a file. Finding files and reading their length needs no ffmpeg (mutagen reads
the headers), so the app can check them while the text is typed; decoding happens only on render.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from .script import Script, ScriptError, Sound, SoundOptions, fmt_time
from .stitch import trim_silence

SOUND_EXTS = (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".opus", ".flac")
DEFAULT_DIR = "sounds"
EDGE_FADE_MS = 10           # every sound fades in and out at least this much, so a cut never clicks
SILENCE_DBFS = -50.0        # quieter edges are trimmed; the timeline's gaps decide the silence
END_SLACK_MS = 100          # header lengths can be a little off


@dataclass(frozen=True)
class SoundFile:
    """What the sounds folder holds for one sound name."""

    name: str
    caption: str
    line: int                       # first use
    path: Optional[Path] = None     # None = not found
    length_ms: Optional[int] = None   # None = unknown or not found
    problem: str = ""               # why it can't be used; "" = fine


def default_folder(tagged_file) -> Path:
    """The sounds folder for a tagged file: sounds/ next to it."""
    return Path(tagged_file).parent / DEFAULT_DIR


def find_sounds(script: Script, folder: Optional[Path]) -> List[SoundFile]:
    """One entry per sound name in *script*, in order of first use. Never raises."""
    first: Dict[str, Sound] = {}
    for s in script.sounds:
        first.setdefault(s.name, s)
    listing = _listing(folder)
    return [_find(s, folder, listing) for s in first.values()]


def attach_sounds(script: Script, folder: Optional[Path]) -> List[SoundFile]:
    """Look up every sound's file and check its options against the file's length.

    Raises one ScriptError listing every problem (at the line of the sound's first use, or of the
    cue whose options don't fit); otherwise sets `script.sound_files` and returns the lookup.
    """
    found = find_sounds(script, folder)
    problems = [(script.path, f.line, f.problem) for f in found if f.problem]
    lengths = {f.name: f.length_ms for f in found}
    for s in script.sounds:
        problem = _range_problem(s, lengths.get(s.name))
        if problem:
            problems.append((script.path, s.line, problem))
    if problems:
        raise ScriptError(sorted(problems, key=lambda p: p[1]))
    script.sound_files = {f.name: f.path for f in found}
    return found


def load_sound(path: Path, opts: SoundOptions, rate: int, channels: int, target: float,
               mode: str = "lufs") -> "AudioSegment":  # noqa: F821
    """Decode the part of *path* that plays, level it with the speech and apply the fades."""
    seg = trim_silence(_decode(path, opts, rate, channels), SILENCE_DBFS, keep_ms=0)
    if len(seg) == 0:
        raise RuntimeError(f"{path.name}: nothing to play (silent, or start/end leave nothing)")
    seg = seg.apply_gain(sound_gain(seg, target, mode, opts.gain_db))
    fade_in = max(EDGE_FADE_MS, opts.fade_in_ms)
    fade_out = max(EDGE_FADE_MS, opts.fade_out_ms)
    if fade_in + fade_out > len(seg):           # only for a clip shorter than its fades
        fade_in = fade_out = len(seg) // 2
    return seg.fade_in(fade_in).fade_out(fade_out)


def sound_gain(seg, target: float, mode: str = "lufs", volume_db: float = 0.0,
               peak_ceiling_dbfs: float = -1.0) -> float:
    """Gain (dB) that brings a sound to the speech *target* plus *volume_db*.

    A quiet, dynamic recording is not boosted past the peak ceiling: its 16-bit samples would clip.
    """
    from .loudness import measure_lufs

    if seg.max_dBFS == float("-inf"):
        return 0.0
    level = measure_lufs(seg) if mode == "lufs" else None
    if level is None:
        level = seg.dBFS + (3.0 if mode == "lufs" else 0.0)   # same estimate as stitch.role_gains
    return min(target + volume_db - level, peak_ceiling_dbfs - seg.max_dBFS)


# ---------------------------------------------------------------- helpers

def _listing(folder: Optional[Path]) -> Optional[Dict[str, Path]]:
    """File name -> path for the folder, or None if there is no such folder."""
    if folder is None:
        return None
    try:
        return {p.name: p for p in Path(folder).iterdir() if p.is_file()}
    except OSError:
        return None


def _find(s: Sound, folder: Optional[Path], listing: Optional[Dict[str, Path]]) -> SoundFile:
    base = SoundFile(s.name, s.caption, s.line)
    if s.file and Path(s.file).expanduser().is_absolute():
        path = Path(s.file).expanduser()
        if not path.is_file():
            return _with(base, problem=f"sound {s.name}: file not found: {path}")
        return _with(base, path=path, length_ms=length_ms(path))
    if folder is None:
        return _with(base, problem=f"sound {s.name}: no sounds folder (keep the text next to a "
                                   f"'{DEFAULT_DIR}' folder, or choose one with --sounds)")
    if listing is None:
        return _with(base, problem=f"sound {s.name}: the sounds folder {folder} does not exist "
                                   f"(create it and put {s.file or s.name + '.mp3'} there)")
    if s.file:
        path = Path(folder) / s.file
        if not path.is_file():
            return _with(base, problem=f"sound {s.name}: {s.file} is not in {folder}")
        return _with(base, path=path, length_ms=length_ms(path))
    hits = sorted(p for name, p in listing.items()
                  if Path(name).stem.casefold() == s.name.casefold() and Path(name).suffix.lower() in SOUND_EXTS)
    if not hits:
        return _with(base, problem=f"sound {s.name}: put {s.name}.mp3 (or .wav, .m4a, .ogg, .flac) in {folder}")
    if len(hits) > 1:
        return _with(base, problem=f"sound {s.name}: {', '.join(p.name for p in hits)} all match; keep one, "
                                   f"or name the file in @sounds ({s.name} = file)")
    return _with(base, path=hits[0], length_ms=length_ms(hits[0]))


def _with(base: SoundFile, **changes) -> SoundFile:
    return SoundFile(**{**base.__dict__, **changes})


def length_ms(path: Path) -> Optional[int]:
    """Length from the file header (no decoding); None if unknown."""
    try:
        import mutagen

        info = mutagen.File(path)
        return round(info.info.length * 1000) if info is not None and info.info.length else None
    except Exception:  # noqa: BLE001 - an unreadable header only means the length is unknown
        return None


def _range_problem(s: Sound, length: Optional[int]) -> Optional[str]:
    if length is None:
        return None
    o = s.opts
    if o.start_ms >= length:
        return f"sound {s.name}: start={fmt_time(o.start_ms)} is past the end of the file ({fmt_time(length)})"
    if o.end_ms is not None and o.end_ms > length + END_SLACK_MS:
        return f"sound {s.name}: end={fmt_time(o.end_ms)} is past the end of the file ({fmt_time(length)})"
    plays = min(o.end_ms if o.end_ms is not None else length, length) - o.start_ms
    if o.fade_in_ms + o.fade_out_ms > plays:
        return (f"sound {s.name}: fade_in + fade_out ({(o.fade_in_ms + o.fade_out_ms) / 1000:g} s) is longer "
                f"than the part that plays ({plays / 1000:g} s)")
    return None


def _decode(path: Path, opts: SoundOptions, rate: int, channels: int):
    """ffmpeg decodes, cuts, downmixes and resamples (better than pydub's audioop for music)."""
    from pydub import AudioSegment

    from .loudness import ffmpeg_path

    cmd = [ffmpeg_path(), "-hide_banner", "-nostdin", "-v", "error"]
    if opts.start_ms:
        cmd += ["-ss", f"{opts.start_ms / 1000:.3f}"]
    cmd += ["-i", str(path), "-vn"]
    if opts.end_ms is not None:
        cmd += ["-t", f"{(opts.end_ms - opts.start_ms) / 1000:.3f}"]
    cmd += ["-f", "s16le", "-acodec", "pcm_s16le", "-ac", str(channels), "-ar", str(rate), "pipe:1"]
    proc = subprocess.run(cmd, capture_output=True, check=False)
    if proc.returncode != 0 or not proc.stdout:
        why = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        raise RuntimeError(f"can't read {path.name}: {why[-1] if why else 'no audio in the file'}")
    frame = 2 * channels
    data = proc.stdout[:len(proc.stdout) // frame * frame]
    return AudioSegment(data=data, sample_width=2, frame_rate=rate, channels=channels)


def label(f: SoundFile) -> str:
    """'Anthem1.mp3 · 1:02' or the problem, for tables and the app's list."""
    if f.problem or f.path is None:
        return f.problem
    return f"{f.path.name} · {fmt_time(f.length_ms)}" if f.length_ms is not None else f.path.name

