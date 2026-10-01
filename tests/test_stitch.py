import asyncio

from pydub import AudioSegment

from parley.dialog_tts.loudness import measure_lufs
from parley.dialog_tts.render import RenderOptions, render_scripts
from parley.dialog_tts.script import parse_text
from parley.dialog_tts.stitch import Gaps, decode, plan_timeline, trim_silence
from parley.dialog_tts.subtitles import to_srt
from parley.dialog_tts.tts import CachedBackend

from conftest import tone_wav

VOICES = """\
@voices
Narrator = de-CH-JanNeural   rate=-10%
Male1 = de-AT-JonasNeural
Male2 = de-DE-ConradNeural
Male3 = de-DE-KillianNeural
@end
"""

SCRIPT = """\
@voices
Narrator = de-DE-FlorianMultilingualNeural
Male1 = de-AT-JonasNeural
Male2 = de-DE-ConradNeural
@end
[Narrator] Titel.
[pause 1.5]
[Male1] Eins.
[Male1] Zwei.
[Male2] Drei.
[pause 2]
[Narrator] Ende.
"""


def layout(tl):
    return [(i.why, i.start_ms, i.dur_ms) for i in tl.items]


def test_gap_lengths_and_pauses():
    s = parse_text(SCRIPT)
    tl = plan_timeline(s.segments, [1000, 500, 600, 700, 800], Gaps(lead_ms=100, tail_ms=200))
    assert layout(tl) == [
        ("lead", 0, 100),
        ("clip", 100, 1000),
        ("pause", 1100, 1500),        # explicit pause replaces the turn gap
        ("clip", 2600, 500),
        ("gap-same", 3100, 250),      # same speaker
        ("clip", 3350, 600),
        ("gap-change", 3950, 450),    # speaker change
        ("clip", 4400, 700),
        ("pause", 5100, 2000),
        ("clip", 7100, 800),
        ("tail", 7900, 200),
    ]
    assert tl.total_ms == 8100
    assert [(c.start_ms, c.end_ms) for c in tl.cues] == [
        (100, 1100), (2600, 3100), (3350, 3950), (4400, 5100), (7100, 7900)]


def test_configurable_gaps():
    s = parse_text(SCRIPT)
    tl = plan_timeline(s.segments, [10] * 5, Gaps(change_ms=900, same_ms=100))
    whys = {i.why: i.dur_ms for i in tl.items}
    assert whys["gap-change"] == 900 and whys["gap-same"] == 100


def test_shadow_and_repeat():
    s = parse_text(VOICES + "[Narrator] T.\n[Male1 repeat=2] Hallo.\n[Male2] Servus.\n")
    tl = plan_timeline(s.segments, [400, 1000, 500], Gaps(lead_ms=0, tail_ms=0, shadow_factor=1.3))
    assert layout(tl) == [
        ("clip", 0, 400),             # no shadow after the narrator
        ("gap-change", 400, 450),
        ("clip", 850, 1000),
        ("repeat", 1850, 700),
        ("clip", 2550, 1000),
        ("shadow", 3550, 1300),       # 1.3 x clip, after the last repetition
        ("gap-change", 4850, 450),
        ("clip", 5300, 500),
        ("shadow", 5800, 650),
    ]
    assert (tl.cues[1].start_ms, tl.cues[1].end_ms) == (850, 3550)


def test_role_gains_equalise_long_clips():
    from parley.dialog_tts.stitch import role_gains
    clips = [decode(tone_wav(3000, a), "wav") for a in (0.6, 0.15)]
    gains = role_gains(clips, ["A", "B"], -16.0)
    after = [measure_lufs(c.apply_gain(gains[r])) for c, r in zip(clips, "AB")]
    assert all(abs(x + 16.0) < 0.5 for x in after), after


def test_trim_silence():
    seg = decode(tone_wav(1000, 0.5, lead_ms=300, tail_ms=700), "wav")
    assert abs(len(seg) - 2000) <= 1
    trimmed = trim_silence(seg, keep_ms=40)
    assert abs(len(trimmed) - 1080) <= 10


def test_srt_format():
    s = parse_text("# Cast: Kellner = Male3\n" + VOICES + "[Male3] Grüß Gott!\n")
    tl = plan_timeline(s.segments, [1234], Gaps(lead_ms=3_725_500))
    assert to_srt(tl.cues, s.label) == "1\n01:02:05,500 --> 01:02:06,734\nKellner: Grüß Gott!\n"


def test_render_end_to_end_with_fake_backend(tmp_path, fake):
    s = parse_text(SCRIPT, str(tmp_path / "lektion_99.tagged.txt"))
    backend = CachedBackend(fake, tmp_path / "cache")
    opts = RenderOptions(gaps=Gaps(lead_ms=100, tail_ms=200), srt=True, shadow=True, slow=True, clips=True)
    written = asyncio.run(render_scripts([s], backend, tmp_path / "out", opts))
    names = sorted(p.name for p, _ in written)
    assert names == ["lektion_99.mp3", "lektion_99.shadow.mp3", "lektion_99.shadow.srt",
                     "lektion_99.slow.mp3", "lektion_99.slow.srt", "lektion_99.srt", "lektion_99_clips"]
    assert len(fake.calls) == 10                      # 5 lines + 5 slow versions
    assert {spec.rate for _, spec in fake.calls} == {"+0%", "-20%"}

    # timeline with trimmed fake clips (40 ms/char + 2 x 40 ms kept padding)
    clip = [40 * len(u.text) + 80 for u in s.utterances]
    expected = 100 + clip[0] + 1500 + clip[1] + 250 + clip[2] + 450 + clip[3] + 2000 + clip[4] + 200
    main = dict((p.name, ms) for p, ms in written)["lektion_99.mp3"]
    assert abs(main - expected) <= 5 * 3             # rounding per clip
    audio = AudioSegment.from_file(tmp_path / "out/lektion_99.mp3")
    assert abs(len(audio) - main) < 80                # mp3 encoder padding

    # the loud voice (Jonas 0.6) and the quiet one (Conrad 0.15) end up equally loud
    clips = sorted((tmp_path / "out/lektion_99_clips").glob("*.mp3"))
    loud = [measure_lufs(AudioSegment.from_file(c)) for c in clips]
    jonas, conrad = loud[1], loud[3]
    assert abs(jonas - conrad) < 1.0, loud                  # 12 dB apart before normalising

    srt = (tmp_path / "out/lektion_99.srt").read_text(encoding="utf-8")
    assert srt.count("-->") == 5 and "Male1: Eins." in srt


def test_cache_reuse_and_partial_rerender(tmp_path, fake):
    backend = CachedBackend(fake, tmp_path / "cache")
    opts = RenderOptions()
    s = parse_text(SCRIPT, "a.txt")
    asyncio.run(render_scripts([s], backend, tmp_path / "o", opts))
    assert len(fake.calls) == 5
    asyncio.run(render_scripts([s], CachedBackend(fake, tmp_path / "cache"), tmp_path / "o", opts))
    assert len(fake.calls) == 5                       # warm cache: no new requests
    changed = parse_text(SCRIPT.replace("Zwei.", "Zwei!"), "a.txt")
    asyncio.run(render_scripts([changed], CachedBackend(fake, tmp_path / "cache"), tmp_path / "o", opts))
    assert len(fake.calls) == 6 and fake.calls[-1][0] == "Zwei!"


def test_identical_lines_are_synthesised_once(tmp_path, fake):
    s = parse_text(VOICES + "[Male1] Ja.\n[Male2] Nein.\n[Male1] Ja.\n", "b.txt")
    asyncio.run(render_scripts([s], CachedBackend(fake, tmp_path / "c"), tmp_path / "o", RenderOptions()))
    assert len(fake.calls) == 2


def test_album_tag_is_configurable(tmp_path, fake):
    from pydub.utils import mediainfo

    s = parse_text(SCRIPT, str(tmp_path / "talk.tagged.txt"))
    backend = CachedBackend(fake, tmp_path / "c")
    asyncio.run(render_scripts([s], backend, tmp_path / "o", RenderOptions()))
    assert mediainfo(str(tmp_path / "o/talk.mp3"))["TAG"]["album"] == "Parley"
    asyncio.run(render_scripts([s], backend, tmp_path / "o", RenderOptions(album="Deutsch im Ohr")))
    assert mediainfo(str(tmp_path / "o/talk.mp3"))["TAG"]["album"] == "Deutsch im Ohr"
