import io
import math
import struct
import sys
import wave
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

RATE = 24000


def tone_wav(ms: int, amp: float, lead_ms: int = 120, tail_ms: int = 400, rate: int = RATE,
             channels: int = 1) -> bytes:
    """Silence + sine tone + silence, like an edge-tts clip with padding."""
    n_lead, n_tone, n_tail = (rate * x // 1000 for x in (lead_ms, ms, tail_ms))
    samples = [0] * n_lead
    samples += [int(amp * 32767 * math.sin(2 * math.pi * 220 * i / rate)) for i in range(n_tone)]
    samples += [0] * n_tail
    samples = [x for x in samples for _ in range(channels)]
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return buf.getvalue()


def write_sound(folder: Path, name: str, ms: int = 2000, amp: float = 0.05) -> Path:
    """A sound file the user would supply: a 44.1 kHz stereo WAV, quieter than the fake voices."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(tone_wav(ms, amp, rate=44100, channels=2))
    return path


class FakeBackend:
    """Clip length = 40 ms per character; loudness differs per voice (like real voices do)."""

    fmt = "wav"
    AMPS = {"de-AT-JonasNeural": 0.6, "de-DE-ConradNeural": 0.15}

    def __init__(self):
        self.calls = []

    async def synth(self, text, spec):
        self.calls.append((text, spec))
        return tone_wav(40 * len(text), self.AMPS.get(spec.voice, 0.3))


@pytest.fixture
def fake():
    return FakeBackend()
