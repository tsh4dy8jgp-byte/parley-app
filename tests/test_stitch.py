import asyncio

from pydub import AudioSegment

from parley.dialog_tts.loudness import measure_lufs
from parley.dialog_tts.render import RenderOptions, render_scripts
from parley.dialog_tts.script import parse_text
from parley.dialog_tts.sounds import attach_sounds
from parley.dialog_tts.stitch import Gaps, decode, plan_timeline, trim_silence
from parley.dialog_tts.subtitles import to_srt
from parley.dialog_tts.tts import CachedBackend

from conftest import tone_wav, write_sound

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


def test_tags_default_and_configurable(tmp_path, fake):
    from mutagen.id3 import ID3

    from parley.mp3tags import Tags

    s = parse_text(SCRIPT, str(tmp_path / "talk.tagged.txt"))
    backend = CachedBackend(fake, tmp_path / "c")
    asyncio.run(render_scripts([s], backend, tmp_path / "o", RenderOptions(shadow=True)))
    t = ID3(tmp_path / "o/talk.mp3")
    assert (t["TIT2"].text[0], t["TPE1"].text[0], t["TALB"].text[0], t["TCON"].text[0]) == (
        "talk", "Parley", "Parley", "Speech")
    assert ID3(tmp_path / "o/talk.shadow.mp3")["TIT2"].text[0] == "talk.shadow"
    cover = tmp_path / "c.png"
    cover.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    tags = Tags(title="Lesson 2", artist="Anna", album="Deutsch im Ohr", year="2025", track="2/10",
                comment="hi", album_artist="Course", cover=str(cover))
    asyncio.run(render_scripts([s], backend, tmp_path / "o", RenderOptions(tags=tags, shadow=True)))
    t = ID3(tmp_path / "o/talk.mp3")
    assert [t[k].text[0] for k in ("TIT2", "TPE1", "TALB", "TPE2", "TRCK")] == [
        "Lesson 2", "Anna", "Deutsch im Ohr", "Course", "2/10"]
    assert str(t["TDRC"].text[0]) == "2025" and t["COMM::eng"].text[0] == "hi"
    assert t["APIC:Cover"].mime == "image/png"
    assert ID3(tmp_path / "o/talk.shadow.mp3")["TIT2"].text[0] == "Lesson 2 (shadow)"


def test_sounds_get_a_change_gap_on_both_sides():
    s = parse_text(VOICES + "[Male1] Eins.\n[sound Anthem]\n[sound Bell]\n[Male1] Zwei.\n[pause 1]\n"
                   "[sound Bell]\n[Narrator] Ende.\n")
    tl = plan_timeline(s.segments, [400, 2000, 300, 500, 300, 600], Gaps(lead_ms=0, tail_ms=0, shadow_factor=1.3))
    assert [(i.why, i.start_ms, i.dur_ms, i.clip_index) for i in tl.items] == [
        ("clip", 0, 400, 0),
        ("shadow", 400, 520, None),
        ("gap-change", 920, 450, None),
        ("sound", 1370, 2000, 1),       # no shadow gap after a sound
        ("gap-change", 3370, 450, None),
        ("sound", 3820, 300, 2),
        ("gap-change", 4120, 450, None),
        ("clip", 4570, 500, 3),         # the same speaker as before the sounds: still a change gap
        ("shadow", 5070, 650, None),
        ("pause", 5720, 1000, None),    # a pause replaces the gap
        ("sound", 6720, 300, 4),
        ("gap-change", 7020, 450, None),
        ("clip", 7470, 600, 5),
    ]
    assert [type(c.segment).__name__ for c in tl.cues] == ["Utterance", "Sound", "Sound", "Utterance", "Sound",
                                                           "Utterance"]
    assert (tl.cues[1].start_ms, tl.cues[1].end_ms) == (1370, 3370)


def test_render_with_a_sound_is_48k_stereo(tmp_path, fake):
    write_sound(tmp_path / "sounds", "Anthem1.wav", ms=3000, amp=0.05)
    text = ("@sounds\nAnthem1  fade_out=0.5   # Brass band plays the anthem\n@end\n"
            "[Male1] Eins, zwei, drei, vier, fünf.\n[sound Anthem1 start=0.5 end=2]\n[Male2] Zwei.\n")
    s = parse_text(VOICES + text, str(tmp_path / "piece.tagged.txt"))
    attach_sounds(s, tmp_path / "sounds")
    opts = RenderOptions(gaps=Gaps(lead_ms=100, tail_ms=200), srt=True, slow=True)
    written = dict(asyncio.run(render_scripts([s], CachedBackend(fake, tmp_path / "c"), tmp_path / "out", opts)))
    clip = [40 * len(u.text) + 80 for u in s.utterances]
    expected = 100 + clip[0] + 450 + 1500 + 450 + clip[1] + 200
    assert abs(written[tmp_path / "out/piece.mp3"] - expected) <= 15
    audio = AudioSegment.from_file(tmp_path / "out/piece.mp3")
    assert (audio.frame_rate, audio.channels) == (48000, 2)
    assert abs(len(audio) - expected) < 80
    sound = audio[100 + clip[0] + 450 + 200: 100 + clip[0] + 450 + 1000]
    speech = audio[100 + 200: 100 + clip[0] - 200]
    assert abs(measure_lufs(sound) - measure_lufs(speech)) < 1.0         # 0.05 vs 0.6 amplitude before leveling
    srt = (tmp_path / "out/piece.srt").read_text(encoding="utf-8")
    assert "\n♪ Brass band plays the anthem\n" in srt and srt.count("-->") == 3
    slow = AudioSegment.from_file(tmp_path / "out/piece.slow.mp3")
    assert (slow.frame_rate, slow.channels) == (48000, 2)


def test_render_without_sounds_stays_24k_mono(tmp_path, fake):
    s = parse_text(VOICES + "[Male1] Eins.\n", str(tmp_path / "plain.txt"))
    asyncio.run(render_scripts([s], CachedBackend(fake, tmp_path / "c"), tmp_path / "out", RenderOptions()))
    audio = AudioSegment.from_file(tmp_path / "out/plain.mp3")
    assert (audio.frame_rate, audio.channels) == (24000, 1)


def test_render_needs_looked_up_sounds(tmp_path, fake):
    s = parse_text(VOICES + "[Male1] Eins.\n[sound Bell]\n", str(tmp_path / "x.txt"))
    try:
        asyncio.run(render_scripts([s], CachedBackend(fake, tmp_path / "c"), tmp_path / "out", RenderOptions()))
    except RuntimeError as e:
        assert str(e) == "line 8: sound Bell has no file (look it up with attach_sounds)"
    else:
        raise AssertionError("rendered a sound without a file")
