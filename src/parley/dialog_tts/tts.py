"""TTS backends: edge-tts with retries and a concurrency limit, plus a disk cache."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
import tempfile
from pathlib import Path
from typing import Dict, Optional, Protocol, Tuple

from .voices import VoiceSpec


class Backend(Protocol):
    fmt: str  # container format of the returned bytes ("mp3", "wav")

    async def synth(self, text: str, spec: VoiceSpec) -> bytes: ...


class EdgeBackend:
    """edge_tts.Communicate per request; bounded concurrency; retries with backoff."""

    fmt = "mp3"

    def __init__(self, concurrency: int = 4, retries: int = 4, backoff: float = 1.5):
        self._sem = asyncio.Semaphore(max(1, concurrency))
        self.retries = retries
        self.backoff = backoff
        self.calls = 0

    async def synth(self, text: str, spec: VoiceSpec) -> bytes:
        import edge_tts

        last: Optional[Exception] = None
        async with self._sem:
            for attempt in range(1, self.retries + 1):
                try:
                    self.calls += 1
                    comm = edge_tts.Communicate(text, spec.voice, rate=spec.rate,
                                                pitch=spec.pitch, volume=spec.volume)
                    audio = bytearray()
                    async for msg in comm.stream():
                        if msg["type"] == "audio":
                            audio.extend(msg["data"])
                    if not audio:
                        raise RuntimeError("service returned no audio")
                    return bytes(audio)
                except Exception as e:  # noqa: BLE001 - network/service errors are transient
                    last = e
                    if attempt < self.retries:
                        await asyncio.sleep(self.backoff * 2 ** (attempt - 1) + random.random())
        raise RuntimeError(f"TTS failed after {self.retries} attempts for {spec.voice} "
                           f"{text[:40]!r}: {last}")


def cache_key(text: str, spec: VoiceSpec) -> str:
    payload = json.dumps([text, spec.voice, spec.rate, spec.pitch, spec.volume], ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class CachedBackend:
    """Disk cache in front of another backend; identical in-flight requests are shared."""

    def __init__(self, inner: Backend, cache_dir: Path):
        self.inner = inner
        self.fmt = inner.fmt
        self.dir = Path(cache_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.hits = 0
        self.misses = 0
        self._inflight: Dict[str, "asyncio.Future[bytes]"] = {}

    def path_for(self, text: str, spec: VoiceSpec) -> Path:
        return self.dir / f"{cache_key(text, spec)}.{self.fmt}"

    async def synth(self, text: str, spec: VoiceSpec) -> bytes:
        path = self.path_for(text, spec)
        if path.exists():
            self.hits += 1
            return path.read_bytes()
        key = path.name
        if key in self._inflight:
            return await self._inflight[key]
        fut: "asyncio.Future[bytes]" = asyncio.get_running_loop().create_future()
        self._inflight[key] = fut
        try:
            data = await self.inner.synth(text, spec)
            self.misses += 1
            _atomic_write(path, data)
            fut.set_result(data)
            return data
        except BaseException as e:
            fut.set_exception(e)
            fut.exception()  # mark retrieved so an unawaited future does not warn
            raise
        finally:
            del self._inflight[key]


def _atomic_write(path: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".part")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


async def synth_all(backend: Backend, jobs: Dict[Tuple[str, VoiceSpec], None] | list,
                    on_progress=None) -> Dict[Tuple[str, VoiceSpec], bytes]:
    """Synthesize every unique (text, spec) pair concurrently; returns a lookup table."""
    unique = list(dict.fromkeys(jobs))
    done = 0

    async def one(job):
        nonlocal done
        data = await backend.synth(*job)
        done += 1
        if on_progress:
            on_progress(done, len(unique))
        return job, data

    return dict(await asyncio.gather(*(one(j) for j in unique)))
