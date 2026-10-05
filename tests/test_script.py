import pytest

from parley.dialog_tts.script import Pause, ScriptError, Sound, SoundOptions, fmt_time, parse_narration, parse_text
from parley.dialog_tts.voices import VoiceSpec, duplicate_voice_warnings, load_voices_yaml

HEADER = """\
@voices
Narrator = de-AT-IngridNeural   rate=-10%
Male1    = de-AT-JonasNeural    pitch=-6Hz rate=-5%
Male2    = de-DE-ConradNeural
@end
"""


def errors(text, **kw):
    with pytest.raises(ScriptError) as e:
        parse_text(text, "t.txt", **kw)
    return [(line, msg) for _, line, msg in e.value.problems]


def test_valid_file():
    s = parse_text(HEADER + """
# a comment
[Narrator] Lektion zwei. Im Kaffeehaus.
[pause 1.5]

[Male1] Grüß Gott! Bitte schön?
[Male2] Eine Melange, bitte.
""")
    assert [type(x).__name__ for x in s.segments] == ["Utterance", "Pause", "Utterance", "Utterance"]
    narr, pause, m1, m2 = s.segments
    assert narr.spec == VoiceSpec("de-AT-IngridNeural", rate="-10%")
    assert pause == Pause(9, 1.5)
    assert m1.spec == VoiceSpec("de-AT-JonasNeural", rate="-5%", pitch="-6Hz")
    assert (m1.line, m1.text) == (11, "Grüß Gott! Bitte schön?")
    assert m2.role == "Male2"


def test_no_built_in_cast():
    with pytest.raises(ScriptError) as e:
        parse_text("[Male1] Servus!")
    assert "not mapped to a voice" in str(e.value)
    s = parse_text("[Male1] Servus!", base_voices={"Male1": VoiceSpec("de-AT-JonasNeural")})
    assert s.utterances[0].spec.voice == "de-AT-JonasNeural"


def test_untagged_text():
    assert errors(HEADER + "[Male1] Hallo.\nWie geht's?\n") == [
        (7, "untagged text (start the line with [Role], or use --continue-speaker): \"Wie geht's?\"")]


def test_continue_speaker():
    s = parse_text(HEADER + "[Male1] Hallo.\nWie geht's?\n[Male2] Gut.", continue_speaker=True)
    assert [(u.role, u.text) for u in s.utterances] == [
        ("Male1", "Hallo."), ("Male1", "Wie geht's?"), ("Male2", "Gut.")]
    assert errors("Hallo.\n", continue_speaker=True)[0][1] == "untagged text before any speaker tag"


def test_unmapped_role():
    (line, msg), = errors(HEADER + "[Male9] Hallo.\n")
    assert line == 6 and msg.startswith("role 'Male9' is not mapped")


def test_empty_utterance():
    assert errors(HEADER + "[Male1]\n[Male2]   \n") == [
        (6, "empty utterance for role Male1"), (7, "empty utterance for role Male2")]


def test_unknown_control_tags():
    errs = errors(HEADER + "[break 2]\n[pause]\n[pause abc]\n[pause 2] trailing text\n")
    assert [e[0] for e in errs] == [6, 7, 8, 9]
    assert errs[0][1].startswith("unknown control tag [break 2]")
    assert all("bad pause tag" in m for _, m in errs[1:])


def test_bad_voice_lines_and_unclosed_block():
    errs = errors("@voices\nMale1 = de-AT-JonasNeural rate=fast\nMale2 de-DE-ConradNeural\n")
    assert errs == [
        (1, "@voices block is not closed with @end"),
        (2, "rate must look like +10% / -20%, got 'fast'"),
        (3, "bad @voices line (expected 'Role = voice [rate=..] [pitch=..] [volume=..]'): "
            "'Male2 de-DE-ConradNeural'"),
    ]


def test_all_errors_reported_with_line_numbers():
    errs = errors(HEADER + "oops\n[Male1] ok\n[Nobody] hi\n[pause x]\n")
    assert [e[0] for e in errs] == [6, 8, 9]


def test_line_modifiers():
    s = parse_text(HEADER + "[Male1 slow] Langsam.\n[Male2 rate=+10% pitch=-4Hz repeat=3] Nochmal.\n"
                   "[Male2 volume=-30%] Leise.\n")
    slow, rep, soft = s.utterances
    assert slow.spec == VoiceSpec("de-AT-JonasNeural", rate="-25%", pitch="-6Hz")
    assert rep.spec == VoiceSpec("de-DE-ConradNeural", rate="+10%", pitch="-4Hz")
    assert rep.repeat == 3
    assert soft.spec.volume == "-30%"
    assert errors(HEADER + "[Male1 whisper] x\n")[0][1].startswith("unknown line modifier 'whisper'")
    assert errors(HEADER + "[Male1 loud] x\n")[0][1].startswith("unknown line modifier 'loud'")
    assert errors(HEADER + "[Male1 repeat=12] x\n")[0][1].startswith("unknown line modifier 'repeat=12'")


def test_lexicon_and_inline_respelling():
    s = parse_text(HEADER + "@lexicon\nMelange = Melohnsch\n@end\n"
                   "[Male2] Eine Melange, bitte. {Topfen|Toppfen}?\n")
    u = s.utterances[0]
    assert u.text == "Eine Melange, bitte. Topfen?"
    assert u.speak == "Eine Melohnsch, bitte. Toppfen?"


def test_cast_comment_gives_subtitle_labels():
    s = parse_text("# Cast: Kellner = Male1; David = Male2\n" + HEADER + "[Male1] Bitte?")
    assert s.label("Male1") == "Kellner" and s.label("Narrator") == "Narrator"


def test_voices_yaml_merge(tmp_path):
    y = tmp_path / "voices.yaml"
    y.write_text("Male1: de-DE-KillianNeural\nHost: {voice: de-CH-JanNeural, rate: -5%}\n", encoding="utf-8")
    base = load_voices_yaml(y)
    s = parse_text("@voices\nMale1 = de-AT-JonasNeural\n@end\n[Male1] a\n[Host] b\n", base_voices=base)
    assert s.utterances[0].spec.voice == "de-AT-JonasNeural"      # in-file block wins
    assert s.utterances[1].spec == VoiceSpec("de-CH-JanNeural", rate="-5%")


def test_duplicate_voice_warning():
    s = parse_text("@voices\nA = de-AT-JonasNeural\nB = de-AT-JonasNeural\nC = de-AT-JonasNeural pitch=+20Hz\n"
                   "@end\n[A] x\n[B] y\n[C] z\n")
    w = duplicate_voice_warnings(s.voices, [u.role for u in s.utterances])
    assert w[0].startswith("warning: roles A and B use identical settings")
    assert w[1].startswith("note: roles A and C share base voice")
    assert duplicate_voice_warnings(s.voices, ["A", "C"])[0].startswith("note")


def test_course_lexicon_file_merges_and_file_block_wins(tmp_path):
    from parley.dialog_tts.script import load_lexicon
    lex = tmp_path / "lexicon.txt"
    lex.write_text("# comment\nÜbung = Üebung   # trailing comment\nTopfen = Toppfen\n\n", encoding="utf-8")
    base = load_lexicon(lex)
    assert base == {"Übung": "Üebung", "Topfen": "Toppfen"}
    s = parse_text(HEADER + "@lexicon\nTopfen = Topfn\n@end\n[Narrator] Übung. Topfen und Übungen.\n",
                   base_lexicon=base)
    u = s.utterances[0]
    assert u.text == "Übung. Topfen und Übungen."          # subtitles unchanged
    assert u.speak == "Üebung. Topfn und Übungen."         # whole words only; file entry wins


def test_bad_lexicon_file_line(tmp_path):
    from parley.dialog_tts.script import load_lexicon
    lex = tmp_path / "lexicon.txt"
    lex.write_text("Übung Üebung\n", encoding="utf-8")
    with pytest.raises(ScriptError) as e:
        load_lexicon(lex)
    assert e.value.problems[0][1] == 1


def test_multilingual_voices_warn_in_any_language():
    s = parse_text("@voices\nNarrator = de-DE-FlorianMultilingualNeural\nA = en-US-AndrewMultilingualNeural\n"
                   "B = en-US-AvaNeural\n@end\n[Narrator] Übung.\n[A] Hi.\n[B] Hello.\n")
    w = duplicate_voice_warnings(s.voices, ["Narrator", "A", "B"])
    assert [x.split(";")[0] for x in w] == [
        "warning: role Narrator uses multilingual voice de-DE-FlorianMultilingualNeural",
        "warning: role A uses multilingual voice en-US-AndrewMultilingualNeural"]
    assert all("pronunciation may be messy" in x and "German" not in x for x in w)


def test_cli_picks_up_lexicon_next_to_files(tmp_path):
    from parley.dialog_tts.cli import load_scripts
    (tmp_path / "lexicon.txt").write_text("Übung = Üebung\n", encoding="utf-8")
    f = tmp_path / "a.tagged.txt"
    f.write_text(HEADER + "[Narrator] Übung.\n", encoding="utf-8")
    assert load_scripts([f], None, False)[0].utterances[0].speak == "Üebung."


def test_cli_roles_come_from_the_file_or_voices_yaml(tmp_path, capsys):
    from parley.dialog_tts.cli import load_scripts
    f = tmp_path / "a.tagged.txt"
    f.write_text("[Narrator] Hi.\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        load_scripts([f], None, False)                     # no built-in cast
    assert "not mapped to a voice" in capsys.readouterr().err
    y = tmp_path / "voices.yaml"
    y.write_text("Narrator: en-US-AndrewNeural\n", encoding="utf-8")
    assert load_scripts([f], y, False)[0].utterances[0].spec.voice == "en-US-AndrewNeural"


def test_cli_finds_sounds_next_to_the_file_or_in_sounds_dir(tmp_path, capsys):
    from conftest import write_sound

    from parley.dialog_tts import cli

    f = tmp_path / "piece.tagged.txt"
    f.write_text(HEADER + "@sounds\nAnthem1 fade_out=2  # The anthem\n@end\n[Male1] Hallo.\n"
                 "[sound Anthem1 end=2.5]\n", encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        cli.main(["render", str(f), "--dry-run"])
    assert e.value.code == 2
    assert f"piece.tagged.txt:10: sound Anthem1: the sounds folder {tmp_path / 'sounds'} does not exist" \
        in capsys.readouterr().err
    write_sound(tmp_path / "music", "Anthem1.wav")
    cli.main(["render", str(f), "--dry-run", "--sounds", str(tmp_path / "music")])
    row = capsys.readouterr().out.splitlines()[-1].split()
    assert row[:4] == ["10", "[sound]", "Anthem1:", "Anthem1.wav"] and row[-3:] == ["fade_out=2s", "The", "anthem"]
    write_sound(tmp_path / "sounds", "Anthem1.wav")
    assert cli.load_scripts([f], None, False)[0].sound_files == {"Anthem1": tmp_path / "sounds/Anthem1.wav"}
    assert cli.load_scripts([f], None, False, resolve_sounds=False)[0].sound_files == {}


def test_cli_voices_lists_all_or_one_locale_and_checks_files(tmp_path, monkeypatch, capsys):
    import edge_tts

    from parley.dialog_tts import cli

    async def list_voices():
        return [{"ShortName": n, "Gender": "Female", "Locale": n[:5]}
                for n in ("de-DE-KatjaNeural", "en-US-AvaNeural")]

    monkeypatch.setattr(edge_tts, "list_voices", list_voices)
    cli.main(["voices"])
    out = capsys.readouterr().out
    assert "de-DE-KatjaNeural" in out and "en-US-AvaNeural" in out
    cli.main(["voices", "--locale", "en-"])
    out = capsys.readouterr().out
    assert "en-US-AvaNeural" in out and "de-DE" not in out
    f = tmp_path / "a.tagged.txt"
    f.write_text("@voices\nA = en-US-AvaNeural\n@end\n[A] Hi.\n", encoding="utf-8")
    cli.main(["voices", str(f)])                           # files only: just the check
    assert capsys.readouterr().out == "all mapped voices exist\n"


def test_adjust_voice_hook_runs_after_the_voices_block():
    text = ("@voices\nMale1 = de-AT-JonasNeural rate=-5%\nMale2 = de-DE-ConradNeural\n@end\n"
            "[Male1 slow] Servus!\n[Male2] Moin!")
    seen = []

    def adjust(role, spec):
        seen.append(role)
        return spec.shifted(pitch=10) if role == "Male1" else spec

    s = parse_text(text, adjust_voice=adjust)
    m1, m2 = s.utterances
    assert m1.spec == VoiceSpec("de-AT-JonasNeural", rate="-25%", pitch="+10Hz")   # file + hook + 'slow'
    assert m2.spec == VoiceSpec("de-DE-ConradNeural")
    assert "Male1" in seen and "Male2" in seen


def test_course_fixture_parses_on_its_own():
    from pathlib import Path

    from parley.dialog_tts.script import parse_file
    s = parse_file(Path(__file__).parent / "fixtures" / "lektion_02.tagged.txt")
    assert len(s.utterances) == 17
    assert s.voices["Narrator"] == VoiceSpec("de-CH-JanNeural", rate="-10%")


SOUNDS = """\
@sounds
Anthem1                       fade_out=3              # Brass band plays the anthem
Radio1 = "moon landing.mp3"   start=0:04 end=0:31.5
@end
"""


def test_sound_tags_and_block():
    s = parse_text(HEADER + SOUNDS + "[Male1] Hallo.\n[sound Anthem1 fade_in=1.5 fade_out=2s]\n"
                   "[sound Radio1 volume=-3dB]\n[sound Bell]\n[Male2] Servus.\n")
    assert [type(x).__name__ for x in s.segments] == ["Utterance", "Sound", "Sound", "Sound", "Utterance"]
    anthem, radio, bell = s.sounds
    assert anthem == Sound(11, "Anthem1", "", SoundOptions(fade_in_ms=1500, fade_out_ms=2000),  # the tag wins
                           "Brass band plays the anthem")
    assert (radio.file, radio.caption) == ("moon landing.mp3", "Radio1")       # no description: the name
    assert radio.opts == SoundOptions(start_ms=4000, end_ms=31500, gain_db=-3.0)
    assert bell == Sound(13, "Bell", "", SoundOptions(), "Bell")                # undeclared: found by name
    assert s.sound_files == {}                                                  # the parser reads no files


def test_sound_tag_problems():
    errs = errors(HEADER + "[sound]\n[sound 1x]\n[sound A] text\n[sound B foo=1 fade_in=99 volume=+20dB start=x]\n"
                  "[sound C start=5 end=4]\n[Sound D]\n[noise]\n")
    assert [n for n, _ in errs] == [6, 7, 8, 9, 9, 9, 9, 10, 12]
    assert errs[0][1] == "sound tag needs a name: [sound Name]"
    assert errs[2][1].startswith("[sound ...] goes on its own line")
    assert errs[3][1].startswith("unknown sound option 'foo=1' (known: fade_in=S, fade_out=S, start=T")
    assert errs[7][1] == "sound C: end (0:04) must come after start (0:05)"
    assert errs[8][1] == "unknown control tag [noise] (known: pause, sound)"


def test_sound_block_problems():
    errs = errors("@sounds\nA fade_in=x\nA\nA\nB = \n@end\n@music\n")
    assert errs == [(2, "fade_in must be seconds from 0 to 30, like fade_in=2.5, got 'x'"),
                    (4, "sound A is declared twice in @sounds"),
                    (5, "bad @sounds line (expected 'Name [= file] [option=value ...] [# description]'): 'B ='"),
                    (7, "unknown directive '@music' (known: @voices, @lexicon, @sounds, @end)")]


def test_sound_times():
    s = parse_text("[sound A start=90 end=1:35.25]\n[sound B start=2.5s end=0:03]\n")
    assert [(x.opts.start_ms, x.opts.end_ms) for x in s.sounds] == [(90_000, 95_250), (2500, 3000)]
    assert errors("[sound A start=1:75]\n")[0][1] == "start must be a time like 12.5 or 1:05, got '1:75'"
    assert [fmt_time(ms) for ms in (0, 5000, 65_500, 61_250, 600_000)] == ["0:00", "0:05", "1:05.5", "1:01.25",
                                                                         "10:00"]


def test_sound_keeps_the_previous_speaker():
    s = parse_text(HEADER + "[Male1] Hallo.\n[sound Bell]\nWie geht's?\n", continue_speaker=True)
    assert [(u.role, u.text) for u in s.utterances] == [("Male1", "Hallo."), ("Male1", "Wie geht's?")]


def test_parse_narration_with_sounds():
    text = ("# Chapter 1\n@sounds\nRain  fade_in=2   # Rain on the roof\n@end\nIt was late.\nThe rain\nkept on.\n\n"
            "Second paragraph.\n[sound Rain end=8]\n[1] A footnote.\n[pause 1.5]\nThe end.\n")
    narrator = VoiceSpec("en-GB-RyanNeural", rate="-5%")
    s = parse_narration(text, "book", narrator=narrator, split=lambda t: t.split("\n\n"),
                        base_lexicon={"rain": "reign"})
    assert [type(x).__name__ for x in s.segments] == ["Utterance", "Utterance", "Sound", "Utterance", "Pause",
                                                      "Utterance"]
    first, second = s.utterances[:2]
    assert (first.line, first.role, first.spec) == (5, "Narrator", narrator)
    assert (first.text, first.speak) == ("It was late.\nThe rain\nkept on.", "It was late.\nThe reign\nkept on.")
    assert second.text == "Second paragraph."
    assert s.sounds[0] == Sound(10, "Rain", "", SoundOptions(fade_in_ms=2000, end_ms=8000), "Rain on the roof")
    assert s.utterances[2].text == "[1] A footnote."                    # other brackets stay prose
    assert s.voices == {"Narrator": narrator}
    with pytest.raises(ScriptError) as e:
        parse_narration("Text.\n[sound]\n", narrator=narrator)
    assert e.value.problems == [("<text>", 2, "sound tag needs a name: [sound Name]")]
