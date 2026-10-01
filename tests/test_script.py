import pytest

from parley.dialog_tts.script import Pause, ScriptError, parse_text
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


def test_defaults_when_voices_block_missing():
    s = parse_text("[Male1] Servus!\n[Female2] Moin!")
    assert s.utterances[0].spec.voice == "de-AT-JonasNeural"
    assert s.utterances[1].spec.voice == "de-DE-KatjaNeural"


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
    s = parse_text("# Cast: Kellner = Male3; David = Male2\n[Male3] Bitte?")
    assert s.label("Male3") == "Kellner" and s.label("Narrator") == "Narrator"


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
    s = parse_text("@lexicon\nTopfen = Topfn\n@end\n[Narrator] Übung. Topfen und Übungen.\n", base_lexicon=base)
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


def test_multilingual_voice_warning():
    s = parse_text("@voices\nNarrator = de-DE-FlorianMultilingualNeural\n@end\n[Narrator] Übung.\n")
    w = duplicate_voice_warnings(s.voices, ["Narrator"])
    assert len(w) == 1 and "multilingual voice" in w[0]
    assert not any("Multilingual" in v.voice for v in parse_text("[Male1] x").voices.values())


def test_cli_picks_up_lexicon_next_to_files(tmp_path):
    from parley.dialog_tts.cli import load_scripts
    (tmp_path / "lexicon.txt").write_text("Übung = Üebung\n", encoding="utf-8")
    f = tmp_path / "a.tagged.txt"
    f.write_text("[Narrator] Übung.\n", encoding="utf-8")
    assert load_scripts([f], None, False)[0].utterances[0].speak == "Üebung."


def test_adjust_voice_hook_runs_after_the_voices_block():
    text = "@voices\nMale1 = de-AT-JonasNeural rate=-5%\n@end\n[Male1 slow] Servus!\n[Male2] Moin!"
    seen = []

    def adjust(role, spec):
        seen.append(role)
        return spec.shifted(pitch=10) if role == "Male1" else spec

    s = parse_text(text, adjust_voice=adjust)
    m1, m2 = s.utterances
    assert m1.spec == VoiceSpec("de-AT-JonasNeural", rate="-25%", pitch="+10Hz")   # file + hook + 'slow'
    assert m2.spec == VoiceSpec("de-DE-ConradNeural")
    assert "Male1" in seen and "Male2" in seen
