"""Synthesize text chunks and assemble them into a single MP3 audiobook."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Callable, Optional

import edge_tts

from ..mp3tags import Tags, write_tags
from .chunker import DEFAULT_CHUNK_SIZE, split_text

MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 2.0


async def _synthesize_chunk(
    text: str,
    voice: str,
    rate: str,
    volume: str,
    pitch: str,
) -> bytes:
    """Synthesize one chunk, retrying on transient network/service errors."""
    last_error: Optional[Exception] = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            communicate = edge_tts.Communicate(
                text, voice, rate=rate, volume=volume, pitch=pitch
            )
            audio = bytearray()
            async for message in communicate.stream():
                if message["type"] == "audio":
                    audio.extend(message["data"])
            if not audio:
                raise RuntimeError("service returned no audio data")
            return bytes(audio)
        except Exception as error:  # noqa: BLE001 - retry any transient failure
            last_error = error
            if attempt < MAX_RETRIES:
                await asyncio.sleep(RETRY_BACKOFF_SECONDS * attempt)
    raise RuntimeError(f"chunk failed after {MAX_RETRIES} attempts: {last_error}")


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
    tags: Optional[Tags] = None,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> Path:
    """Convert *text* into an MP3 audiobook at *output_path*.

    Chunks are synthesized concurrently (bounded by *concurrency*) and
    written to the output file in their original order. *tags* (if given) are
    written as ID3 tags; an empty title becomes the file name. Edge TTS emits a
    self-contained MP3 stream per request, so streams can be concatenated
    directly.
    """
    chunks = split_text(text, max_chars=max_chars)
    if not chunks:
        raise ValueError("no text to synthesize")

    semaphore = asyncio.Semaphore(max(1, concurrency))
    done = 0

    async def worker(index: int, chunk: str) -> "tuple[int, bytes]":
        nonlocal done
        async with semaphore:
            audio = await _synthesize_chunk(chunk, voice, rate, volume, pitch)
        done += 1
        if on_progress:
            on_progress(done, len(chunks))
        return index, audio

    results = await asyncio.gather(*(worker(i, c) for i, c in enumerate(chunks)))
    results.sort(key=lambda item: item[0])

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as handle:
        for _, audio in results:
            handle.write(audio)
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
