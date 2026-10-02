import asyncio
import json

import pytest

from parley.dialog_tts.script import parse_text
from parley.dialog_tts.voices import VoiceSpec
from parley import catalog as cat
from parley import jobs, settings as st
from parley.guide import EXAMPLES, STEPS, example_for


def voice(name, gender, locale, language=None):
    return {"ShortName": name, "Gender": gender, "Locale": locale,
            "LocaleName": language or locale, "FriendlyName": f"Microsoft X Online - {language}"}


RAW = [
    voice("en-US-AvaNeural", "Female", "en-US", "English (United States)"),
    voice("en-US-AndrewMultilingualNeural", "Male", "en-US", "English (United States)"),
    voice("en-US-AriaNeural", "Female", "en-US", "English (United States)"),
    voice("en-US-GuyNeural", "Male", "en-US", "English (United States)"),
    voice("en-GB-SoniaNeural", "Female", "en-GB", "English (United Kingdom)"),
    voice("kk-KZ-AigulNeural", "Female", "kk-KZ", "Kazakh (Kazakhstan)"),
    voice("kk-KZ-DauletNeural", "Male", "kk-KZ", "Kazakh (Kazakhstan)"),
]


@pytest.fixture
def catalog():
    return cat.Catalog.from_edge(RAW)


# --- settings -----------------------------------------------------------------------------------

def test_settings_round_trip(tmp_path):
    s = st.Settings(language="kk-KZ", srt=True, gap_change=600, target=-18.0)
    s.speakers["A"] = st.Speaker("en-US-AvaNeural", rate=-10, pitch=5)
    st.save(s, tmp_path / "s.json")
    assert st.load(tmp_path / "s.json") == s


def test_settings_tolerate_bad_files(tmp_path):
    p = tmp_path / "s.json"
    assert st.load(p) == st.Settings()                       # missing
    p.write_text("{not json")
    assert st.load(p) == st.Settings()                       # corrupt
    p.write_text(json.dumps({"language": 5, "srt": "yes", "gap_same": 100, "bogus": 1,
                             "target": -20, "speakers": {"A": {"voice": "x", "rate": "fast"},
                                                         "bad name": {"voice": "y"}}}))
    s = st.load(p)
    assert (s.language, s.srt, s.gap_same, s.target) == ("en-US", False, 100, -20.0)
    assert s.speakers == {"A": st.Speaker("x")}


def test_migrate_legacy_moves_tts_studio_files(tmp_path):
    old_config, old_cache = tmp_path / ".config/tts-studio", tmp_path / ".cache/tts-studio"
    config, cache = tmp_path / ".config/parley", tmp_path / ".cache/parley"
    old_cache.mkdir(parents=True)
    (old_cache / "voices.json").write_text("[]")
    st.save(st.Settings(cache_dir=str(old_cache), out_dir="/old/out", srt=True), old_config / "settings.json")
    st.save_draft("[A] Hi", old_config / "draft.txt")

    st.migrate_legacy(config, cache, old_config, old_cache)
    assert not old_config.exists() and not old_cache.exists()
    assert (cache / "voices.json").read_text() == "[]"
    assert st.load_draft(config / "draft.txt") == "[A] Hi"
    s = st.load(config / "settings.json")
    assert (s.cache_dir, s.out_dir, s.srt) == (str(cache), "/old/out", True)   # output folder kept


def test_migrate_legacy_never_overwrites_parley_files(tmp_path):
    old_config, config = tmp_path / "old", tmp_path / "new"
    st.save(st.Settings(srt=True), old_config / "settings.json")
    st.save(st.Settings(), config / "settings.json")
    st.migrate_legacy(config, tmp_path / "cache", old_config, tmp_path / "old_cache")   # no old cache: fine
    assert st.load(config / "settings.json").srt is False
    assert (old_config / "settings.json").exists()


def test_speaker_spec():
    assert st.Speaker("v", rate=-10, pitch=5).spec() == VoiceSpec("v", rate="-10%", pitch="+5Hz")
    auto = VoiceSpec("auto", rate="-5%")
    assert st.Speaker("").spec(auto) == auto                  # no explicit voice -> automatic one
    assert st.Speaker("v").spec(auto) == VoiceSpec("v")       # explicit voice ignores automatic
    assert st.Speaker("", pitch=10).spec(auto) == VoiceSpec("auto", rate="-5%", pitch="+10Hz")


# --- catalog ------------------------------------------------------------------------------------

def test_languages_and_voices(catalog):
    assert catalog.languages() == [("English (United Kingdom)", "en-GB"),
                                   ("English (United States)", "en-US"),
                                   ("Kazakh (Kazakhstan)", "kk-KZ")]
    names = [v.name for v in catalog.voices_for("en-US")]
    assert names[-1] == "en-US-AndrewMultilingualNeural"      # multilingual voices last
    assert catalog.get("en-US-AvaNeural").short == "Ava"
    assert catalog.get("en-US-AvaNeural").label == "Ava · F"


def test_pick_narrator(catalog):
    assert catalog.pick_narrator("en-US") == "en-US-AriaNeural"      # PREFERRED_VOICES["en"]
    assert catalog.pick_narrator("en-US", "Male") == "en-US-GuyNeural"
    assert catalog.pick_narrator("kk-KZ", "Male") == "kk-KZ-DauletNeural"
    with pytest.raises(ValueError):
        catalog.pick_narrator("xx-YY")


def test_auto_cast_distinct_and_alternating(catalog):
    cast = catalog.auto_cast("en-US", ["A", "B", "C"])
    assert cast["A"].voice in ("en-US-AriaNeural", "en-US-AvaNeural")
    assert catalog.get(cast["B"].voice).gender == "Male"
    assert "Multilingual" not in cast["B"].voice
    assert len(set(cast.values())) == 3


def test_auto_cast_reuses_voices_with_prosody_shift(catalog):
    cast = catalog.auto_cast("kk-KZ", ["A", "B", "C", "D", "E"])
    assert len(set(cast.values())) == 5                      # still all distinguishable
    assert cast["C"].voice == cast["A"].voice and cast["C"] != cast["A"]


def test_auto_cast_gender_hints(catalog):
    cast = catalog.auto_cast("en-US", ["Male1", "Female1"])
    assert catalog.get(cast["Male1"].voice).gender == "Male"
    assert catalog.get(cast["Female1"].voice).gender == "Female"


def test_catalog_cache_and_fallback(tmp_path, monkeypatch):
    cache = tmp_path / "voices.json"
    cache.write_text(json.dumps(RAW))
    assert cat.load_cached(cache).get("kk-KZ-AigulNeural") is not None
    fb = cat.load_cached(tmp_path / "missing.json")
    assert fb.offline and fb.pick_narrator("de-DE") == "de-DE-KatjaNeural"


# --- jobs ---------------------------------------------------------------------------------------

def test_detect():
    assert jobs.detect("").mode == "empty"
    d = jobs.detect("Once upon a time.\n[1] footnote\nThe end.")
    assert (d.mode, d.roles, d.words) == ("narration", [], 8)
    d = jobs.detect("# c\n[B] Hi\n[pause 1]\n[A slow] Yo\n[B] Bye")
    assert (d.mode, d.roles) == ("dialogue", ["B", "A"])
    d = jobs.detect("@voices\nA = en-US-AvaNeural\n@end\n@lexicon\nX = y\n@end\n[A] Hi\n[B] Yo")
    assert (d.mode, d.roles, d.pinned) == ("dialogue", ["A", "B"], ["A"])


def test_build_cast_precedence(catalog):
    s = st.Settings(language="en-US")
    s.speakers["B"] = st.Speaker("en-GB-SoniaNeural", pitch=10)
    s.speakers["C"] = st.Speaker("", rate=-10)                   # automatic voice, slower
    cast = jobs.build_cast(s, ["A", "B", "C"], catalog)
    auto = catalog.auto_cast("en-US", ["A", "B", "C"])
    assert cast["A"] == auto["A"]
    assert cast["B"] == VoiceSpec("en-GB-SoniaNeural", pitch="+10Hz")
    assert cast["C"] == auto["C"].shifted(rate=-10)


def test_build_cast_is_automatic_for_german_too(de_catalog):
    cast = jobs.build_cast(st.Settings(language="de-DE"), ["Male1", "X"], de_catalog)
    assert set(cast) == {"Male1", "X"}                          # no built-in course cast
    assert all(v.voice.startswith("de-") for v in cast.values())


def test_validate_reports_problems_and_cast(catalog):
    s = st.Settings(language="en-US")
    check = jobs.validate("[A] Hi\n[A text\n[B foo] Yo", s, catalog)
    assert [n for n, _ in check.problems] == [2, 3]
    assert "--continue-speaker" not in check.problems[0][1]          # GUI wording, not the CLI flag
    assert "continue the previous speaker" in check.problems[0][1]
    ok = jobs.validate("[A] Hi\n[B] Yo", s, catalog)
    assert not ok.problems and set(ok.voices) == {"A", "B"}


def test_validate_file_voices_block_wins(catalog):
    text = "@voices\nA = en-GB-SoniaNeural\n@end\n[A] Hi"
    assert jobs.validate(text, st.Settings(), catalog).voices["A"].voice == "en-GB-SoniaNeural"


def test_validate_warns_about_a_multilingual_narrator(catalog):
    prose = "Just some prose."
    assert jobs.validate(prose, st.Settings(language="en-US"), catalog).warnings == []   # automatic voice
    s = st.Settings(language="en-US", narrator=st.Speaker("en-US-AndrewMultilingualNeural"))
    (w,) = jobs.validate(prose, s, catalog).warnings
    assert w.startswith("warning: the narrator uses multilingual voice en-US-AndrewMultilingualNeural;")


def test_render_options_mapping():
    s = st.Settings(gap_change=600, srt=True, slow=True, loudness="dbfs", target=-20.0)
    o = jobs.render_options(s)
    assert (o.gaps.change_ms, o.srt, o.slow, o.loudness, o.target) == (600, True, True, "dbfs", -20.0)
    assert (o.tags.artist, o.tags.album, o.tags.title) == ("Parley", "Parley", "")


def test_safe_name():
    assert jobs.safe_name(" lektion_02.tagged.txt ") == "lektion_02"
    assert jobs.safe_name("my talk.mp3") == "my talk"
    assert jobs.safe_name("chapter_01.md") == "chapter_01"
    assert jobs.safe_name("notes.markdown") == "notes"
    assert jobs.safe_name("a/b:c") == "a_b_c"
    assert jobs.safe_name("  ") == "untitled"


def test_planned_outputs(tmp_path):
    s = st.Settings(out_dir=str(tmp_path), out_name="x", shadow=True, srt=True)
    assert [p.name for p in jobs.planned_outputs(s, "dialogue")] == ["x.mp3", "x.srt", "x.shadow.mp3",
                                                                     "x.shadow.srt"]
    assert [p.name for p in jobs.planned_outputs(s, "narration")] == ["x.mp3"]


def test_run_dialogue_with_fake_backend(tmp_path, fake, catalog):
    s = st.Settings(language="en-US", out_dir=str(tmp_path / "out"), out_name="talk", srt=True,
                    cache_dir=str(tmp_path / "cache"))
    seen = []
    written = asyncio.run(jobs.run_dialogue("[A] Hello there.\n[B] Hi!", s, catalog,
                                            lambda d, t: seen.append((d, t)), backend=fake))
    assert [p.name for p, _ in written] == ["talk.mp3", "talk.srt"]
    assert seen[-1] == (2, 2)
    assert "A: Hello there." in (tmp_path / "out/talk.srt").read_text(encoding="utf-8")


def test_lexicon_next_to_source_file(tmp_path, catalog):
    (tmp_path / "lexicon.txt").write_text("Melange = Melahnsch\n", encoding="utf-8")
    src = tmp_path / "l.tagged.txt"
    check = jobs.validate("[A] Eine Melange.", st.Settings(), catalog, source=src)
    assert check.script.utterances[0].speak == "Eine Melahnsch."


def test_runner_reports_done_error_and_cancel():
    import queue

    events = queue.Queue()

    async def ok(progress):
        progress(1, 1)
        return 42

    async def boom(progress):
        raise RuntimeError("nope")

    async def slow(progress):
        await asyncio.sleep(10)

    def drain(runner):
        runner.join(5)
        out = []
        while not events.empty():
            out.append(events.get())
        return [(e.type, e.payload) for e in out]

    r = jobs.Runner(events, "gen")
    r.start(ok)
    assert drain(r) == [("progress", (1, 1)), ("done", 42)]
    r.start(boom)
    ((kind, err),) = drain(r)
    assert kind == "error" and str(err) == "nope"
    r.start(slow)
    r.cancel()
    assert drain(r) == [("cancelled", None)]


# --- guide --------------------------------------------------------------------------------------

@pytest.mark.parametrize("lang", sorted(EXAMPLES))
def test_guide_examples_parse(lang, catalog):
    text = EXAMPLES[lang]
    d = jobs.detect(text)
    assert d.mode == "dialogue" and d.roles[:3] == ["A", "B", "C"]
    check = jobs.validate(text, st.Settings(language="en-US"), catalog)
    assert not check.problems, check.problems


def test_guide_samples_are_valid_lines(catalog):
    base = {r: VoiceSpec("en-US-AvaNeural") for r in ("A", "B", "C", "Anna")}
    for step in STEPS:
        if step.sample and step.valid:
            parse_text(step.sample, base_voices=base)


def test_example_for_language():
    assert example_for("de-AT") == EXAMPLES["de"]
    assert example_for("kk-KZ") == EXAMPLES["en"]


LESSON = ("@voices\nNarrator = de-CH-JanNeural rate=-10%\nMale2 = de-DE-ConradNeural\n"
          "Male3 = de-DE-KillianNeural\n@end\n[Narrator] Hallo.\n[Male2 slow] Eine Melange.\n[Male3] Gerne.")


def test_advanced_speaker_settings_win_over_voices_block(catalog):
    s = st.Settings(language="de-DE")
    s.speakers["Male2"] = st.Speaker("de-AT-JonasNeural", pitch=20)       # pick a voice + pitch
    s.speakers["Narrator"] = st.Speaker("", rate=-20)                     # keep the file's voice, slower
    narr, m2, m3 = jobs.parse(LESSON, s, catalog).utterances
    assert narr.spec == VoiceSpec("de-CH-JanNeural", rate="-30%")         # file -10 % + slider -20 %
    assert m2.spec == VoiceSpec("de-AT-JonasNeural", rate="-20%", pitch="+20Hz")   # 'slow' still applies
    assert m3.spec == VoiceSpec("de-DE-KillianNeural")                    # untouched: file voice


def test_validate_reports_the_voices_actually_used(catalog):
    s = st.Settings(language="de-DE")
    s.speakers["Male3"] = st.Speaker("", volume=-10)
    assert jobs.validate(LESSON, s, catalog).voices["Male3"] == VoiceSpec("de-DE-KillianNeural", volume="-10%")


def test_sliders_apply_to_automatic_voices(de_catalog):
    s = st.Settings(language="de-DE")
    auto = jobs.build_cast(s, ["Male3"], de_catalog)["Male3"]
    s.speakers["Male3"] = st.Speaker("", rate=-30)
    assert jobs.build_cast(s, ["Male3"], de_catalog)["Male3"] == VoiceSpec(auto.voice, rate="-30%")


@pytest.fixture
def de_catalog():
    return cat.Catalog.from_edge(RAW + [
        voice("de-DE-ConradNeural", "Male", "de-DE", "German (Germany)"),
        voice("de-DE-KatjaNeural", "Female", "de-DE", "German (Germany)"),
        voice("de-DE-KillianNeural", "Male", "de-DE", "German (Germany)"),
        voice("de-CH-JanNeural", "Male", "de-CH", "German (Switzerland)"),
    ])


def use(text, s, catalog):
    return jobs.language_use(jobs.validate(text, s, catalog), s, catalog)


def test_language_fixed_by_voices_block(de_catalog):
    u = use(LESSON, st.Settings(language="en-US"), de_catalog)
    assert (u.state, u.source, u.language, u.auto_roles) == ("fixed", "text", "German", [])


def test_language_fixed_by_advanced_voices(de_catalog):
    s = st.Settings(language="en-US")
    s.speakers.update({"A": st.Speaker("de-DE-KatjaNeural"), "B": st.Speaker("en-GB-SoniaNeural")})
    u = use("[A] Hallo\n[B] Hello", s, de_catalog)
    assert (u.state, u.source, u.language) == ("fixed", "advanced", "German, English")


def test_language_mismatch_offers_the_texts_locale(de_catalog):
    text = "@voices\nA = de-DE-KatjaNeural\nC = de-CH-JanNeural\nD = de-DE-ConradNeural\n@end\n" \
           "[A] Hallo\n[B] Servus\n[C] Grüezi\n[D] Moin"
    u = use(text, st.Settings(language="en-US"), de_catalog)
    assert (u.state, u.language, u.auto_roles, u.suggested) == ("mismatch", "German", ["B"], "de-DE")
    assert use(text, st.Settings(language="de-AT"), de_catalog).state == "free"     # same language: fine


def test_language_free_cases(de_catalog):
    s = st.Settings(language="en-US")
    assert use("[A] Hi\n[B] Yo", s, de_catalog).state == "free"                     # all automatic
    assert use("Just some prose.", s, de_catalog).state == "free"
    assert use("", s, de_catalog).state == "free"


def test_language_fixed_for_narration_with_picked_voice(de_catalog):
    s = st.Settings(language="en-US", narrator=st.Speaker("de-DE-KatjaNeural"))
    u = use("Just some prose.", s, de_catalog)
    assert (u.state, u.source, u.language) == ("fixed", "advanced", "German")


def test_engines_import_without_tk():
    """The CLIs must work without the gui extra, so the engines may not pull in the app."""
    import subprocess
    import sys
    from pathlib import Path

    code = ("import sys, parley.dialog_tts.cli, parley.audiobook.cli\n"
            "loaded = {'tkinter', 'customtkinter'} & set(sys.modules)\n"
            "assert not loaded, loaded")
    src = Path(__file__).resolve().parents[1] / "src"   # cwd=src so the root parley.py can't shadow the package
    r = subprocess.run([sys.executable, "-c", code], cwd=src, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_tags_from_settings_and_cover_check(tmp_path):
    s = st.Settings(artist="Me", album="Book", genre="Audiobook", year="2024")
    t = jobs.tags_for(s)
    assert (t.artist, t.album, t.genre, t.year, t.title) == ("Me", "Book", "Audiobook", "2024", "")
    assert jobs.cover_problem(s) is None
    assert "not found" in jobs.cover_problem(st.Settings(cover=str(tmp_path / "x.png")))
    (tmp_path / "x.gif").write_bytes(b"GIF")
    assert ".jpg or .png" in jobs.cover_problem(st.Settings(cover=str(tmp_path / "x.gif")))


def test_narration_mp3_gets_tags(tmp_path):
    from mutagen.id3 import ID3

    from parley.audiobook import builder

    async def fake(*_a, **_k):
        return b"\xff\xfb\x90\x00" + b"\x00" * 200
    old, builder._synthesize_chunk = builder._synthesize_chunk, fake
    try:
        out = tmp_path / "book.mp3"
        asyncio.run(builder.build_audiobook("Hello world.", "en-US-GuyNeural", out,
                                            tags=jobs.tags_for(st.Settings(artist="Me"))))
    finally:
        builder._synthesize_chunk = old
    t = ID3(out)
    assert (t["TIT2"].text[0], t["TPE1"].text[0], t["TALB"].text[0]) == ("book", "Me", "Parley")
