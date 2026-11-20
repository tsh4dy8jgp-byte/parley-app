"""TTS backends: edge-tts with retries and a concurrency limit, plus a disk cache.

A `Policy` decides how hard Parley tries. `FAST` is the plain behaviour: a few quick retries per
line, and the run stops at the first line that still fails. `SAFE` (Safe Mode, the default in the
app and both CLIs) is built from what the edge-tts issue tracker reports: fewer parallel requests
with a gap between starts, one shared pause when the service answers 429/503, a check that each
clip is not cut short, and rounds at the end of the run for the lines that failed, so one voice
that stops answering for a few minutes does not hold up the others.
"""

from __future__ import annotations

import asyncio
import contextvars
import errno
import hashlib
import io
import json
import os
import random
import re
import socket
import tempfile
import time
from collections import Counter, deque
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Deque, Dict, List, Optional, Protocol, Sequence, Tuple

from ..tts_errors import TTSError, describe_tts_error
from .voices import VoiceSpec

Job = Tuple[str, VoiceSpec]

# Hears (seconds, lines, round, rounds) before Safe Mode waits to retry failed lines. A ContextVar so
# the app's Runner and the CLIs can listen without every engine function passing a callback along.
WaitListener = Callable[[float, int, int, int], None]
wait_listener: "contextvars.ContextVar[Optional[WaitListener]]" = contextvars.ContextVar(
    "parley_wait_listener", default=None)

_now = time.monotonic    # tests replace this; asyncio's own clock must stay untouched


@dataclass(frozen=True)
class Policy:
    concurrency: int = 4                     # parallel requests
    attempts: int = 4                        # tries per line in one round
    backoff: float = 1.5                     # seconds before the 2nd try; doubles after that
    start_gap: float = 0.0                   # least seconds between two request starts
    throttle_pause: float = 0.0              # all requests wait this long after a 429/503 (doubles, max 60)
    defer_after: int = 0                     # failed lines in a row before a voice's other lines wait a round
    stop_round_after: int = 0                # failed lines in a row, any voice, before the whole round waits
    round_pauses: Tuple[float, ...] = ()     # pauses before the extra rounds; () = stop at the first failure
    check_length: bool = False               # retry clips too short for their text (cut off)
    deadline: bool = False                   # give each request a time limit

    @property
    def safe(self) -> bool:
        return bool(self.round_pauses)


FAST = Policy()
SAFE = Policy(concurrency=2, attempts=3, backoff=2.0, start_gap=0.5, throttle_pause=10.0, defer_after=2,
              stop_round_after=4, round_pauses=(60.0, 120.0), check_length=True, deadline=True)


def policy_for(safe_mode: bool, concurrency: int) -> Policy:
    """SAFE or FAST with the user's parallel requests; Safe Mode uses at most SAFE.concurrency."""
    n = max(1, int(concurrency))
    return replace(SAFE, concurrency=min(n, SAFE.concurrency)) if safe_mode else replace(FAST, concurrency=n)


class Backend(Protocol):
    fmt: str  # container format of the returned bytes ("mp3", "wav")
    # A backend may also have `policy: Policy`; synth_all runs Safe Mode's rounds when it is SAFE.

    async def synth(self, text: str, spec: VoiceSpec) -> bytes: ...


class SpeechRejected(TTSError):
    """Retrying can't help: the request is invalid, or Microsoft rejects this client (HTTP 401/403)."""


class _BadRequest(Exception):
    """edge-tts refused the parameters before sending anything (e.g. a malformed voice name)."""


class _ClipTooShort(RuntimeError):
    pass


class EdgeBackend:
    """edge_tts.Communicate per request, run as *policy* says (FAST unless told otherwise)."""

    fmt = "mp3"

    def __init__(self, policy: Policy = FAST, **overrides):
        self.policy = replace(policy, **overrides) if overrides else policy
        self._sem = asyncio.Semaphore(max(1, self.policy.concurrency))
        self._next_start = 0.0      # _now() before which no request may start (start_gap)
        self._resume_at = 0.0       # _now() before which no request may start (throttled)
        self._throttled = 0         # 429/503 answers in a row
        self.calls = 0

    async def synth(self, text: str, spec: VoiceSpec) -> bytes:
        import edge_tts

        p = self.policy
        last: Optional[Exception] = None
        short: List[float] = []     # lengths of clips that came back too short, across attempts
        async with self._sem:
            for attempt in range(1, p.attempts + 1):
                await self._wait_turn()
                try:
                    self.calls += 1
                    audio = await self._fetch(edge_tts, text, spec)
                    if p.check_length:
                        _check_length(audio, text, spec, short)
                    self._throttled = 0
                    return audio
                except Exception as e:  # noqa: BLE001 - network/service errors are transient
                    last = e
                    if p.safe and is_fatal(e):
                        cause = e.__cause__ if isinstance(e, _BadRequest) else e
                        raise SpeechRejected(f"{spec.voice}: {describe_tts_error(cause)}") from cause
                    self._note_throttle(e)
                    if attempt < p.attempts:
                        await asyncio.sleep(p.backoff * 2 ** (attempt - 1) + random.random())
        raise TTSError(f"TTS failed after {p.attempts} attempts for {spec.voice} "
                       f"{text[:40]!r}: {describe_tts_error(last)}") from last

    async def _fetch(self, edge_tts, text: str, spec: VoiceSpec) -> bytes:
        try:
            comm = edge_tts.Communicate(text, spec.voice, rate=spec.rate, pitch=spec.pitch, volume=spec.volume)
        except (ValueError, TypeError) as e:
            raise _BadRequest(str(e)) from e
        audio = bytearray()

        async def read() -> None:
            async for msg in comm.stream():
                if msg["type"] == "audio":
                    audio.extend(msg["data"])

        if self.policy.deadline:
            limit = request_deadline(text)
            loop = asyncio.get_running_loop()
            began = loop.time()
            try:
                await asyncio.wait_for(read(), timeout=limit)
            except asyncio.TimeoutError:
                if loop.time() - began < limit:      # edge-tts' own connect/receive timeout: keep its error
                    raise
                raise RuntimeError(f"no complete answer within {limit:.0f} s") from None
        else:
            await read()
        if not audio:
            raise RuntimeError("service returned no audio")
        return bytes(audio)

    async def _wait_turn(self) -> None:
        """Keep start_gap between request starts and wait out a throttle pause (FAST: no wait)."""
        while True:
            wait = max(self._resume_at, self._next_start) - _now()
            if wait <= 0:
                break
            await asyncio.sleep(wait)
        if self.policy.start_gap:
            self._next_start = _now() + self.policy.start_gap

    def _note_throttle(self, error: BaseException) -> None:
        if self.policy.throttle_pause and http_status(error) in (429, 503):
            self._throttled += 1
            pause = min(60.0, self.policy.throttle_pause * 2 ** (self._throttled - 1))
            self._resume_at = max(self._resume_at, _now() + pause)


def http_status(error: Optional[BaseException]) -> Optional[int]:
    from aiohttp import ClientResponseError

    return error.status if isinstance(error, ClientResponseError) else None


def is_fatal(error: BaseException) -> bool:
    """Errors no retry fixes: bad parameters, or a rejected client (edge-tts already retried a 403
    once with the server's clock; what remains needs an edge-tts update or a correct system date)."""
    return isinstance(error, (_BadRequest, SpeechRejected)) or http_status(error) in (401, 403)


def is_offline(error: BaseException) -> bool:
    """The computer can't reach the service at all: no DNS answer, or no route to the network."""
    from aiohttp import ClientConnectorError

    if not isinstance(error, ClientConnectorError):
        return False
    os_error = getattr(error, "os_error", None)
    return isinstance(os_error, socket.gaierror) or getattr(os_error, "errno", None) in (
        errno.ENETUNREACH, errno.EHOSTUNREACH, errno.ENETDOWN)


def request_deadline(text: str) -> float:
    """Seconds a request may take in Safe Mode: about ten times a normal answer, so only a hung
    connection runs into it."""
    return 20.0 + len(text) / 20


# --- the cut-off check -------------------------------------------------------------------------
FASTEST_UNITS_PER_SECOND = 25.0     # about twice normal speech; a clip faster than this lost words
_WIDE = ((0x3400, 0x9FFF), (0xF900, 0xFAFF), (0xAC00, 0xD7AF), (0x20000, 0x2FFFF))   # CJK, Hangul
_RATE = re.compile(r"^\s*([+-]?\d+)\s*%\s*$")


def speech_units(text: str) -> float:
    """Letters and digits; a CJK ideograph or Hangul syllable (a whole syllable) counts as two."""
    units = 0.0
    for ch in text:
        if ch.isalnum():
            o = ord(ch)
            units += 2.0 if any(lo <= o <= hi for lo, hi in _WIDE) else 1.0
    return units


def shortest_plausible(text: str, spec: VoiceSpec) -> float:
    """The least seconds *text* can take at *spec*'s rate; a shorter clip was cut off."""
    m = _RATE.match(spec.rate)
    speed = max(0.5, 1 + int(m.group(1)) / 100) if m else 1.0
    return speech_units(text) / (FASTEST_UNITS_PER_SECOND * speed)


def mp3_seconds(data: bytes) -> Optional[float]:
    from mutagen.mp3 import MP3

    try:
        return MP3(io.BytesIO(data)).info.length
    except Exception:  # noqa: BLE001 - unreadable here; decoding reports it later
        return None


def _check_length(audio: bytes, text: str, spec: VoiceSpec, short: List[float]) -> None:
    """Raise _ClipTooShort for a clip too short for its text, unless an earlier try came back just
    as short: a cut is random, so the same length twice is what the voice really says."""
    seconds = mp3_seconds(audio)
    if seconds is None:
        return
    least = shortest_plausible(text, spec)
    if seconds >= least or any(abs(seconds - s) <= 0.1 * max(seconds, s) for s in short):
        return
    short.append(seconds)
    raise _ClipTooShort(f"the clip is {seconds:.1f} s, too short for its text (cut off)")


# --- cache -------------------------------------------------------------------------------------
def cache_key(text: str, spec: VoiceSpec) -> str:
    payload = json.dumps([text, spec.voice, spec.rate, spec.pitch, spec.volume], ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class CachedBackend:
    """Disk cache in front of another backend; identical in-flight requests are shared."""

    def __init__(self, inner: Backend, cache_dir: Path):
        self.inner = inner
        self.fmt = inner.fmt
        self.policy: Optional[Policy] = getattr(inner, "policy", None)
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


# --- running every line ------------------------------------------------------------------------
class SynthesisFailed(TTSError):
    """Safe Mode gave up on some lines. The first line of the message says what failed and what to
    do (the app shows only that line); callers add where each line is through `where`."""

    def __init__(self, failures: Dict[Job, BaseException], worked: Sequence[str], rounds: int,
                 seconds: float, cached: bool, offline: bool = False):
        super().__init__()
        self.failures = failures
        self.worked = list(worked)       # voices that produced at least one line
        self.rounds = rounds
        self.seconds = seconds
        self.cached = cached
        self.offline = offline
        self.where: Dict[Job, List[str]] = {}    # job -> ["lektion_02 line 12", ...]

    def __str__(self) -> str:
        jobs = list(self.failures)
        n = len(jobs)
        voices = list(dict.fromkeys(spec.voice for _, spec in jobs))
        causes = Counter(describe_tts_error(e) for e in self.failures.values())
        cause = causes.most_common(1)[0][0]
        if self.offline:
            return f"Can't reach Microsoft's speech service ({cause}). Check the internet connection, then try again."
        lines = f"{n} line{'s' * (n != 1)}"
        places = [w for job in jobs for w in self.where.get(job, [])]
        if places:
            lines += f" ({', '.join(places[:4])}{', …' if len(places) > 4 else ''})"
        who = voices[0] if len(voices) == 1 else f"{len(voices)} voices ({', '.join(voices)})"
        if [v for v in self.worked if v not in voices]:
            verdict = (" The other voices worked, so "
                       + ("this voice is" if len(voices) == 1 else "these voices are")
                       + " probably down at Microsoft for now.")
        elif not self.worked:
            verdict = " No voice worked: the service may be down, or it is limiting requests from this network."
        else:
            verdict = ""
        again = "; only the missing lines will be made" if self.cached else ""
        # What to do comes before the verdict: the app's status bar has room for about 150 characters.
        head = (f"No audio from {who} for {lines} after {_duration(self.seconds)} of retries. "
                f"Try again in a few minutes{again}.{verdict}")
        detail = [f"Last error: {cause}"]
        for text, spec in jobs[:8]:
            where = ", ".join(self.where.get((text, spec), []))
            detail.append(f"  {where + ': ' if where else ''}{spec.voice}: {text[:50]!r}")
        if n > 8:
            detail.append(f"  … and {n - 8} more")
        return "\n".join([head, *detail])


def _duration(seconds: float) -> str:
    return f"{seconds:.0f} s" if seconds < 90 else f"{seconds / 60:.0f} min"


async def synth_all(backend: Backend, jobs: Dict[Job, None] | list,
                    on_progress=None) -> Dict[Job, bytes]:
    """Synthesize every unique (text, spec) pair concurrently; returns a lookup table.

    With a Safe Mode policy on the backend, a line that fails waits for the next round instead of
    stopping the run (see _synth_in_rounds); otherwise the first failure propagates.
    """
    unique = list(dict.fromkeys(jobs))
    policy = getattr(backend, "policy", None)
    if policy is not None and policy.safe:
        return await _synth_in_rounds(backend, unique, policy, on_progress)
    done = 0

    async def one(job):
        nonlocal done
        data = await backend.synth(*job)
        done += 1
        if on_progress:
            on_progress(done, len(unique))
        return job, data

    return dict(await asyncio.gather(*(one(j) for j in unique)))


async def _synth_in_rounds(backend: Backend, unique: List[Job], policy: Policy,
                           on_progress) -> Dict[Job, bytes]:
    """Round 1 makes every line; failed lines get policy.round_pauses more rounds, each after a pause.

    Inside a round, a voice whose lines fail defer_after times in a row is skipped for the rest of
    that round, so the other voices finish while it recovers; after stop_round_after failed lines in
    a row of any voice, the service is failing and the rest of the round waits for the next one.
    SpeechRejected stops everything. Lines a round skipped go first in the next one, so a voice that
    works is not held up by failing voices whose lines come before its own.
    """
    results: Dict[Job, bytes] = {}
    worked: Dict[str, None] = {}
    failures: Dict[Job, BaseException] = {}
    untried: set = set()
    failed_at: List[float] = []                  # _now() at the first failure: the retries start there
    pending = unique
    rounds = len(policy.round_pauses) + 1
    cached = isinstance(backend, CachedBackend)
    started = _now()
    rnd = 0
    for rnd in range(1, rounds + 1):
        if rnd > 1:
            pause = policy.round_pauses[rnd - 2]
            listener = wait_listener.get()
            if listener:
                listener(pause, len(pending), rnd, rounds)
            await asyncio.sleep(pause)
            pending = [j for j in pending if j in untried] + [j for j in pending if j not in untried]
        made, hits = len(results), getattr(backend, "hits", 0)
        failures, untried = await _one_round(backend, pending, policy, results, worked, len(unique), on_progress,
                                             failed_at)
        pending = [j for j in pending if j in failures]
        if not pending:
            return results
        fetched = len(results) - made - (getattr(backend, "hits", 0) - hits)   # answers from the service
        if rnd == 1 and not fetched and _all_offline(failures):     # no request got through at all
            break
    retried = _now() - (failed_at or [started])[0]
    failures = {j: failures[j] for j in unique if j in failures}       # in script order again
    raise SynthesisFailed(failures, list(worked), rnd, retried, cached, offline=_all_offline(failures))


def _all_offline(failures: Dict[Job, BaseException]) -> bool:
    return all(is_offline(e) for e in failures.values())


async def _one_round(backend: Backend, pending: List[Job], policy: Policy, results: Dict[Job, bytes],
                     worked: Dict[str, None], total: int, on_progress,
                     failed_at: List[float]) -> Tuple[Dict[Job, BaseException], set]:
    """One pass over *pending*; returns (failed jobs and their errors, the failed jobs never tried)."""
    failures: Dict[Job, BaseException] = {}
    untried: set = set()
    streak: Counter = Counter()                  # voice -> lines failed in a row this round
    failing = [0]                                # lines failed in a row, any voice
    last_error: Dict[str, BaseException] = {}
    queue: Deque[Job] = deque(pending)

    async def worker() -> None:
        while queue:
            job = queue.popleft()
            voice = job[1].voice
            if policy.stop_round_after and failing[0] >= policy.stop_round_after:
                failures[job] = last_error["*"]         # not tried: the service is failing right now
                untried.add(job)
                continue
            if policy.defer_after and streak[voice] >= policy.defer_after:
                failures[job] = last_error[voice]       # not tried: this voice is failing right now
                untried.add(job)
                continue
            try:
                data = await backend.synth(*job)
            except SpeechRejected:
                raise
            except Exception as e:  # noqa: BLE001 - this line waits for the next round
                if not failed_at:
                    failed_at.append(_now())
                failures[job] = e
                streak[voice] += 1
                failing[0] += 1
                last_error[voice] = last_error["*"] = e
                continue
            streak[voice] = 0
            failing[0] = 0
            results[job] = data
            worked[voice] = None
            if on_progress:
                on_progress(len(results), total)

    workers = [asyncio.ensure_future(worker()) for _ in range(max(1, min(policy.concurrency, len(queue))))]
    try:
        await asyncio.gather(*workers)
    finally:
        for w in workers:
            w.cancel()
        await asyncio.gather(*workers, return_exceptions=True)   # let them finish before the loop closes
    return failures, untried
