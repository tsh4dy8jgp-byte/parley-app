"""Safe Mode: pacing, throttle pauses, the cut-off check and retry rounds, without contacting Microsoft."""

import asyncio
import json
import queue
import socket
from collections import Counter
from types import SimpleNamespace

import edge_tts
import pytest
from aiohttp import ClientConnectorError, WSServerHandshakeError
from yarl import URL

from conftest import tone_wav
from parley import jobs
from parley import settings as st
from parley.audiobook import builder
from parley.dialog_tts import cli, tts
from parley.dialog_tts.render import RenderOptions, render_scripts
from parley.dialog_tts.script import ScriptError, parse_narration, parse_text
from parley.dialog_tts.tts import (FAST, SAFE, CachedBackend, EdgeBackend, SpeechRejected, SynthesisFailed,
                                   policy_for, shortest_plausible, synth_all, wait_listener)
from parley.dialog_tts.voices import VoiceSpec

ANNA, BEN = "de-DE-KatjaNeural", "de-DE-ConradNeural"
DIALOGUE = f"""@voices
Anna = {ANNA}
Ben  = {BEN}
@end
[Anna] Guten Morgen, Ben.
[Ben] Guten Morgen, Anna.
[Anna] Wie geht es dir heute?
[Ben] Danke, sehr gut.
[Anna] Das freut mich.
[Ben] Bis morgen.
"""

FRAME = bytes([0xFF, 0xF3, 0x64, 0xC4]) + bytes(140)    # one MPEG-2 layer III frame: 24 ms at 24 kHz


def mp3(seconds: float) -> bytes:
    return FRAME * max(1, round(seconds / 0.024))


def http_error(status: int):
    request = SimpleNamespace(real_url=URL("https://speech.example/?Sec-MS-GEC=secret"))
    return WSServerHandshakeError(request, (), status=status, message="Invalid response status")


def offline_error():
    key = SimpleNamespace(host="speech.platform.bing.com", port=443, ssl=True)
    return ClientConnectorError(key, socket.gaierror(8, "nodename nor servname provided"))


@pytest.fixture
def clock(monkeypatch):
    """A fake clock: asyncio.sleep advances it at once and is recorded."""
    state = SimpleNamespace(now=1000.0, sleeps=[])

    async def sleep(seconds):
        state.sleeps.append(seconds)
        state.now += max(0.0, seconds)

    monkeypatch.setattr(tts, "_now", lambda: state.now)
    monkeypatch.setattr(asyncio, "sleep", sleep)
    return state


class Flaky:
    """Fake backend with a Safe Mode policy; voices in *down* fail for that many calls (None: always)."""

    fmt = "wav"

    def __init__(self, down=None, error=None, policy=SAFE):
        self.policy = policy
        self.down = dict(down or {})
        self.error = error or RuntimeError("No audio was received.")
        self.calls = []

    async def synth(self, text, spec):
        self.calls.append((text, spec.voice))
        left = self.down.get(spec.voice, 0)
        if left is None or left > 0:
            if left:
                self.down[spec.voice] = left - 1
            raise self.error
        return tone_wav(40 * len(text), 0.3)


def script():
    return parse_text(DIALOGUE, "lektion_09.tagged.txt")


def jobs_of(s):
    return [(u.speak, u.spec) for u in s.utterances]


# --- policy -------------------------------------------------------------------------------------

def test_safe_mode_caps_parallel_requests_and_off_keeps_them():
    assert policy_for(True, 8).concurrency == 2 and policy_for(True, 1).concurrency == 1
    assert policy_for(False, 6).concurrency == 6 and policy_for(False, 0).concurrency == 1
    assert policy_for(True, 4).safe and not policy_for(False, 4).safe
    assert policy_for(False, 4) == FAST


def test_settings_default_to_safe_mode_also_for_old_files(tmp_path):
    assert st.Settings().safe_mode is True
    old = tmp_path / "settings.json"
    old.write_text(json.dumps({"concurrency": 4}), encoding="utf-8")        # saved before Safe Mode
    assert st.load(old).safe_mode is True
    old.write_text(json.dumps({"safe_mode": False}), encoding="utf-8")
    assert st.load(old).safe_mode is False


def test_app_backend_follows_the_switch(tmp_path):
    on = jobs.make_backend(st.Settings(cache_dir=str(tmp_path), concurrency=6))
    off = jobs.make_backend(st.Settings(cache_dir=str(tmp_path), concurrency=6, safe_mode=False))
    assert on.policy == policy_for(True, 6) and on.policy.concurrency == 2
    assert off.policy == policy_for(False, 6) and off.policy.concurrency == 6


def test_cli_turns_safe_mode_off_only_when_asked():
    parser = cli.build_parser()
    assert parser.parse_args(["render", "a.txt"]).safe_mode is True
    assert parser.parse_args(["render", "a.txt", "--no-safe-mode"]).safe_mode is False


# --- rounds -------------------------------------------------------------------------------------

def test_a_voice_that_drops_out_is_retried_after_the_others(clock):
    s = script()
    backend = Flaky(down={BEN: 2})                 # Ben's next two requests fail, then he is back
    heard = []
    token = wait_listener.set(lambda *a: heard.append(a))
    try:
        audio = asyncio.run(synth_all(backend, jobs_of(s)))
    finally:
        wait_listener.reset(token)
    assert set(audio) == set(jobs_of(s))
    # Round 1: Ben fails twice in a row, so his third line waits for round 2 without a request.
    first_round, second_round = backend.calls[:-3], backend.calls[-3:]
    assert Counter(v for _, v in first_round) == {ANNA: 3, BEN: 2}
    assert [v for _, v in second_round] == [BEN] * 3
    assert heard == [(60.0, 3, 2, 3)] and 60.0 in clock.sleeps
    assert 120.0 not in clock.sleeps               # round 2 delivered the rest


def test_gives_up_after_the_last_round_and_names_voice_and_lines(clock, tmp_path):
    s = script()
    backend = CachedBackend(Flaky(down={BEN: None}), tmp_path / "cache")
    with pytest.raises(SynthesisFailed) as caught:
        asyncio.run(render_scripts([s], backend, tmp_path / "out", RenderOptions()))
    e = caught.value
    assert [p for p in clock.sleeps if p >= 60] == [60.0, 120.0]
    assert set(e.failures) == {j for j in jobs_of(s) if j[1].voice == BEN}
    head = str(e).splitlines()[0]
    assert f"No audio from {BEN} for 3 lines (line 6, line 8, line 10)" in head
    assert "other voices worked" in head and "only the missing lines will be made" in head
    assert "Last error: No audio was received." in str(e)
    assert len(list((tmp_path / "cache").iterdir())) == 3      # Anna's lines are kept for the next run
    assert not (tmp_path / "out").exists()


def test_a_failing_service_ends_the_round_early(clock):
    cast = "\n".join(f"R{i} = de-DE-Voice{i}Neural" for i in range(6))
    lines = "\n".join(f"[R{i % 6}] Satz Nummer {i}." for i in range(30))
    s = parse_text(f"@voices\n{cast}\n@end\n{lines}\n")
    backend = Flaky(down={f"de-DE-Voice{i}Neural": None for i in range(6)})
    with pytest.raises(SynthesisFailed) as caught:
        asyncio.run(synth_all(backend, jobs_of(s)))
    # Each of the 3 rounds stops after 4 failed lines in a row (plus the one already in flight),
    # instead of trying 2 lines of every voice.
    assert len(backend.calls) <= 3 * (SAFE.stop_round_after + 1)
    assert len(caught.value.failures) == 30


def test_no_voice_working_points_at_the_service_or_network(clock):
    with pytest.raises(SynthesisFailed) as caught:
        asyncio.run(synth_all(Flaky(down={ANNA: None, BEN: None}), jobs_of(script())))
    head = str(caught.value).splitlines()[0]
    assert "2 voices" in head and "No voice worked" in head
    assert "missing lines" not in head             # nothing was cached


def test_offline_stops_after_the_first_round(clock):
    with pytest.raises(SynthesisFailed) as caught:
        asyncio.run(synth_all(Flaky(down={ANNA: None, BEN: None}, error=offline_error()), jobs_of(script())))
    assert caught.value.offline and not [p for p in clock.sleeps if p >= 60]
    assert str(caught.value).startswith("Can't reach Microsoft's speech service")


def test_a_rejected_client_stops_everything_at_once(clock):
    backend = Flaky(down={BEN: None}, error=SpeechRejected("de-DE-ConradNeural: HTTP 403"))
    with pytest.raises(SpeechRejected):
        asyncio.run(synth_all(backend, jobs_of(script())))
    assert not [p for p in clock.sleeps if p >= 60]


def test_safe_mode_off_stops_at_the_first_failure(clock):
    backend = Flaky(down={BEN: 1}, policy=FAST)
    with pytest.raises(RuntimeError, match="No audio"):
        asyncio.run(synth_all(backend, jobs_of(script())))
    assert not clock.sleeps


def test_narration_parts_are_named_when_they_fail(clock, tmp_path):
    text = "Erster Absatz.\n\nZweiter Absatz."
    backend = Flaky(down={ANNA: None})
    with pytest.raises(SynthesisFailed) as caught:
        asyncio.run(builder.build_audiobook(text, ANNA, tmp_path / "b.mp3", max_chars=20, backend=backend))
    assert "(part 1, part 2)" in str(caught.value).splitlines()[0]


def test_runner_reports_waiting(monkeypatch):
    events = queue.Queue()

    async def job(progress):
        wait_listener.get()(60.0, 3, 2, 3)
        return "ok"

    runner = jobs.Runner(events, "generate")
    runner.start(job)
    runner.join(5)
    got = [events.get_nowait() for _ in range(events.qsize())]
    assert [(e.type, e.payload) for e in got] == [("waiting", (60.0, 3, 2, 3)), ("done", "ok")]
    assert wait_listener.get() is None             # only the runner's task heard it


# --- the edge-tts backend -----------------------------------------------------------------------

def fake_communicate(monkeypatch, answers):
    """edge_tts.Communicate that plays *answers* in order: bytes are audio, exceptions are raised."""
    calls = []

    class Fake:
        def __init__(self, text, voice, **kw):
            calls.append(SimpleNamespace(text=text, voice=voice, started=tts._now()))
            self.answer = answers[min(len(calls), len(answers)) - 1]

        async def stream(self):
            if isinstance(self.answer, BaseException):
                raise self.answer
            yield {"type": "audio", "data": self.answer}

    monkeypatch.setattr(edge_tts, "Communicate", Fake)
    return calls


LINE = "Guten Morgen, wie geht es Ihnen heute an diesem schönen Tag?"       # 49 letters: >= 2 s


def test_cut_off_clip_is_made_again(clock, monkeypatch):
    calls = fake_communicate(monkeypatch, [mp3(0.6), mp3(4.0)])
    audio = asyncio.run(EdgeBackend(SAFE).synth(LINE, VoiceSpec(ANNA)))
    assert len(calls) == 2 and audio == mp3(4.0)


def test_same_short_length_twice_is_accepted(clock, monkeypatch):
    calls = fake_communicate(monkeypatch, [mp3(0.6), mp3(0.62)])
    assert asyncio.run(EdgeBackend(SAFE).synth(LINE, VoiceSpec(ANNA))) == mp3(0.62)
    assert len(calls) == 2


def test_length_check_is_off_without_safe_mode(clock, monkeypatch):
    calls = fake_communicate(monkeypatch, [mp3(0.6)])
    asyncio.run(EdgeBackend(FAST).synth(LINE, VoiceSpec(ANNA)))
    assert len(calls) == 1


def test_shortest_plausible_length():
    spec = VoiceSpec(ANNA)
    assert shortest_plausible("Hallo", spec) == pytest.approx(5 / 25)
    assert shortest_plausible("...!?", spec) == 0
    assert shortest_plausible("Hallo", spec.shifted(rate=+25)) == pytest.approx(5 / 25 / 1.25)
    assert shortest_plausible("Hallo", spec.shifted(rate=-20)) == pytest.approx(5 / 25 / 0.8)
    assert shortest_plausible("你好", spec) == pytest.approx(4 / 25)          # ideographs count twice
    assert shortest_plausible("안녕", spec) == pytest.approx(4 / 25)


def test_403_stops_at_once_in_safe_mode(clock, monkeypatch):
    calls = fake_communicate(monkeypatch, [http_error(403)])
    with pytest.raises(SpeechRejected) as caught:
        asyncio.run(EdgeBackend(SAFE).synth(LINE, VoiceSpec(ANNA)))
    assert len(calls) == 1
    assert "HTTP 403" in str(caught.value) and "secret" not in str(caught.value)


def test_bad_voice_name_stops_at_once_in_safe_mode(clock):
    with pytest.raises(SpeechRejected, match="Invalid voice"):
        asyncio.run(EdgeBackend(SAFE).synth(LINE, VoiceSpec("not a voice")))


def test_503_pauses_every_request(clock, monkeypatch):
    calls = fake_communicate(monkeypatch, [http_error(503), mp3(4.0), mp3(4.0)])
    backend = EdgeBackend(SAFE)

    async def two():
        await backend.synth(LINE, VoiceSpec(ANNA))
        await backend.synth(LINE + " Ja.", VoiceSpec(BEN))

    asyncio.run(two())
    assert calls[1].started - calls[0].started >= SAFE.throttle_pause
    assert backend._throttled == 0                     # reset by the success


def test_requests_start_at_least_the_gap_apart(clock, monkeypatch):
    calls = fake_communicate(monkeypatch, [mp3(4.0)])
    backend = EdgeBackend(SAFE)

    async def three():
        await asyncio.gather(*(backend.synth(f"{LINE} {i}", VoiceSpec(ANNA)) for i in range(3)))

    asyncio.run(three())
    starts = [c.started for c in calls]
    assert all(b - a >= SAFE.start_gap for a, b in zip(starts, starts[1:]))


def test_hung_request_runs_into_the_deadline(monkeypatch):
    class Hung:
        def __init__(self, *a, **k):
            pass

        async def stream(self):
            await asyncio.Event().wait()
            yield {}

    monkeypatch.setattr(edge_tts, "Communicate", Hung)
    monkeypatch.setattr(tts, "request_deadline", lambda text: 0.01)
    backend = EdgeBackend(SAFE, attempts=1)
    with pytest.raises(tts.TTSError, match="no complete answer within"):
        asyncio.run(backend.synth(LINE, VoiceSpec(ANNA)))


# --- lines with nothing to say ------------------------------------------------------------------

def test_a_line_without_letters_is_a_problem():
    with pytest.raises(ScriptError) as caught:
        parse_text(f"@voices\nAnna = {ANNA}\n@end\n[Anna] Hallo.\n[Anna] ...\n")
    assert ":5: nothing to say in '...'" in str(caught.value)
    assert "[pause N]" in str(caught.value)


def test_narration_skips_a_separator_with_nothing_to_say(tmp_path):
    s = parse_narration("Erstes Kapitel.\n[pause 1]\n* * *\n[pause 1]\nZweites Kapitel.",
                        narrator=VoiceSpec(ANNA))
    assert [u.text for u in s.utterances] == ["Erstes Kapitel.", "Zweites Kapitel."]


def test_audiobook_skips_a_separator_chunk(clock, tmp_path):
    backend = Flaky()
    out = tmp_path / "b.mp3"
    asyncio.run(builder.build_audiobook("Eins.\n\n* * *\n\nZwei.", ANNA, out, max_chars=8, backend=backend))
    assert [t for t, _ in backend.calls] == ["Eins.", "Zwei."]


def test_a_working_voice_is_not_held_up_by_two_failing_ones(clock):
    cast = "A = de-DE-AmalaNeural\nB = de-DE-BerndNeural\nC = de-DE-ChristophNeural"
    lines = "\n".join(["[A] Eins.", "[B] Zwei.", "[A] Drei.", "[B] Vier.", "[A] Fünf.", "[B] Sechs."]
                      + [f"[C] Satz {i}." for i in range(5)])
    s = parse_text(f"@voices\n{cast}\n@end\n{lines}\n")
    backend = Flaky(down={"de-DE-AmalaNeural": None, "de-DE-BerndNeural": None})
    with pytest.raises(SynthesisFailed) as caught:
        asyncio.run(synth_all(backend, jobs_of(s)))
    # Round 1 stops after 4 failed lines in a row; C's lines, never tried there, go first in round 2.
    assert [v for _, v in backend.calls].count("de-DE-ChristophNeural") == 5
    assert {spec.voice for _, spec in caught.value.failures} == {"de-DE-AmalaNeural", "de-DE-BerndNeural"}


def test_losing_the_network_mid_run_waits_for_the_next_round(clock):
    s = script()

    class Drops(Flaky):
        async def synth(self, text, spec):
            if len(self.calls) >= 2:
                self.down = {ANNA: None, BEN: None}
            return await super().synth(text, spec)

    with pytest.raises(SynthesisFailed) as caught:
        asyncio.run(synth_all(Drops(error=offline_error()), jobs_of(s)))
    assert [p for p in clock.sleeps if p >= 60] == [60.0, 120.0]      # two lines got through: not offline yet
    assert caught.value.offline                                    # but the message says what it was
