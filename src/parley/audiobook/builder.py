"""Synthesize text chunks and assemble them into a single MP3 audiobook."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable, Optional

from ..mp3tags import Tags, write_tags
from .chunker import DEFAULT_CHUNK_SIZE, speakable, split_text


async def build_audiobook(
    text: str,
    voice: str,
    output_path: Path,
    *,
    rate: str = "+0%",
    volume: str = "+0%",
    pitch: str = "+0Hz",
    max_chars: int = DEFAULT_CHUNK_SIZE,
    concurrency: int = 4,
    safe_mode: bool = True,
    tags: Optional[Tags] = None,
    on_progress: Optional[Callable[[int, int], None]] = None,
    backend=None,
) -> Path:
    """Convert *text* into an MP3 audiobook at *output_path*.

    Chunks are synthesized concurrently (at most *concurrency* at a time; Safe Mode, on by default,
    uses at most 2 and retries failed chunks in rounds, see dialog_tts.tts.SAFE) and written to the
    output file in their original order. Chunks with nothing to say ("* * *") are skipped. *tags*
    (if given) are written as ID3 tags; an empty title becomes the file name. Edge TTS emits a
    self-contained MP3 stream per request, so streams can be concatenated directly. *backend*
    replaces the edge-tts backend (tests).
    """
    # Imported here: dialog_tts imports audiobook.voices, so a module-level import would be circular.
    from ..dialog_tts.tts import EdgeBackend, SynthesisFailed, policy_for, synth_all
    from ..dialog_tts.voices import VoiceSpec

    chunks = [c for c in split_text(text, max_chars=max_chars) if speakable(c)]
    if not chunks:
        raise ValueError("no text to synthesize")

    spec = VoiceSpec(voice, rate=rate, pitch=pitch, volume=volume)
    jobs = [(chunk, spec) for chunk in chunks]
    backend = backend or EdgeBackend(policy_for(safe_mode, concurrency))
    try:
        audio = await synth_all(backend, jobs, on_progress)
    except SynthesisFailed as error:
        for i, job in enumerate(jobs, 1):
            error.where.setdefault(job, []).append(f"part {i}")
        raise

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as handle:
        for job in jobs:
            handle.write(audio[job])
    if tags is not None:
        write_tags(output_path, tags.with_title(output_path.stem))
    return output_path


def print_progress(done: int, total: int) -> None:
    """Default progress reporter: a single updating line on stderr."""
    width = 30
    filled = int(width * done / total)
    bar = "#" * filled + "-" * (width - filled)
    end = "\n" if done == total else ""
    sys.stderr.write(f"\r[{bar}] {done}/{total} chunks{end}")
    sys.stderr.flush()


def print_waiting(seconds: float, chunks: int, round_: int, rounds: int) -> None:
    """Safe Mode is about to wait before trying failed chunks again."""
    sys.stderr.write(f"\n{chunks} chunk{'s' * (chunks != 1)} got no audio yet; trying again in "
                     f"{seconds:.0f} s (round {round_} of {rounds})\n")
    sys.stderr.flush()
