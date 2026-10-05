import pytest

from parley.dialog_tts.loudness import measure_lufs
from parley.dialog_tts.script import ScriptError, SoundOptions, parse_text
from parley.dialog_tts.sounds import attach_sounds, default_folder, find_sounds, label, load_sound

from conftest import write_sound


def problems(text, folder):
    with pytest.raises(ScriptError) as e:
        attach_sounds(parse_text(text, "piece.txt"), folder)
    return [(line, msg) for _, line, msg in e.value.problems]


def test_found_by_name_case_insensitively(tmp_path):
    write_sound(tmp_path / "sounds", "anthem1.WAV", ms=1500)
    (tmp_path / "sounds" / "Anthem1.txt").write_text("notes")             # not audio: ignored
    s = parse_text("@sounds\nAnthem1  # The anthem\n@end\n[sound Anthem1]\n[sound Anthem1 fade_in=1]\n", "p.txt")
    (found,) = attach_sounds(s, default_folder(tmp_path / "p.txt"))
    assert (found.name, found.caption, found.line, found.path.name) == ("Anthem1", "The anthem", 4, "anthem1.WAV")
    assert abs(found.length_ms - 2020) < 30                                  # tone + padding of the file
    assert s.sound_files == {"Anthem1": tmp_path / "sounds" / "anthem1.WAV"}
    assert label(found).startswith("anthem1.WAV · 0:02")


def test_explicit_file_in_the_folder_or_absolute(tmp_path):
    write_sound(tmp_path / "sounds", "moon landing.wav")
    other = write_sound(tmp_path / "elsewhere", "bell.wav")
    s = parse_text(f'@sounds\nRadio1 = "moon landing.wav"\nBell = {other}\n@end\n[sound Radio1]\n[sound Bell]\n')
    attach_sounds(s, tmp_path / "sounds")
    assert s.sound_files == {"Radio1": tmp_path / "sounds" / "moon landing.wav", "Bell": other}


def test_missing_and_ambiguous_files(tmp_path):
    folder = tmp_path / "sounds"
    write_sound(folder, "Song1.mp3.wav")                                     # stem "Song1.mp3": no match
    write_sound(folder, "Bell.wav")
    write_sound(folder, "bell.ogg")
    text = "@sounds\nRadio1 = radio.mp3\n@end\n[sound Song1]\n[sound Bell]\n[sound Radio1]\n[sound Song1]\n"
    assert problems(text, folder) == [
        (4, f"sound Song1: put Song1.mp3 (or .wav, .m4a, .ogg, .flac) in {folder}"),
        (5, "sound Bell: Bell.wav, bell.ogg all match; keep one, or name the file in @sounds (Bell = file)"),
        (6, f"sound Radio1: radio.mp3 is not in {folder}")]
    assert problems("[sound X]\n", tmp_path / "nope") == [
        (1, f"sound X: the sounds folder {tmp_path / 'nope'} does not exist (create it and put X.mp3 there)")]
    assert problems("[sound X]\n", None)[0][1].startswith("sound X: no sounds folder (keep the text next to")


def test_find_sounds_never_raises(tmp_path):
    write_sound(tmp_path, "A.wav")
    found = find_sounds(parse_text("[sound B]\n[sound A]\n[sound B]\n"), tmp_path)
    assert [(f.name, f.line, bool(f.problem), f.path is not None) for f in found] == [
        ("B", 1, True, False), ("A", 2, False, True)]


def test_options_are_checked_against_the_length(tmp_path):
    write_sound(tmp_path, "A.wav", ms=2000)                                  # 2.52 s with its padding
    text = "[sound A start=3]\n[sound A end=4]\n[sound A start=1 fade_in=1 fade_out=1]\n[sound A end=2.6]\n"
    assert problems(text, tmp_path) == [
        (1, "sound A: start=0:03 is past the end of the file (0:02.52)"),
        (2, "sound A: end=0:04 is past the end of the file (0:02.52)"),
        (3, "sound A: fade_in + fade_out (2 s) is longer than the part that plays (1.52 s)")]


def test_load_sound_cuts_levels_and_fades(tmp_path):
    path = write_sound(tmp_path, "A.wav", ms=3000, amp=0.05)
    seg = load_sound(path, SoundOptions(start_ms=500, end_ms=2500, fade_out_ms=500), 48000, 2, -16.0)
    assert (seg.frame_rate, seg.channels, seg.sample_width) == (48000, 2, 2)
    assert abs(len(seg) - 2000) <= 10                                        # 0.5-2.5 s, all tone
    assert abs(measure_lufs(seg[:1400]) + 16.0) < 1.0                       # leveled to the speech target
    assert seg[-20:].max < seg[:200].max / 4                                 # faded out
    quieter = load_sound(path, SoundOptions(gain_db=-6.0), 48000, 2, -16.0)
    assert abs(measure_lufs(quieter[200:2200]) + 22.0) < 1.0
    lead = load_sound(path, SoundOptions(), 48000, 2, -16.0)
    assert abs(len(lead) - 3000) <= 10                                       # the file's silent edges are trimmed


def test_quiet_dynamic_sound_is_not_boosted_into_clipping(tmp_path):
    path = write_sound(tmp_path, "A.wav", ms=2000, amp=0.9)
    seg = load_sound(path, SoundOptions(gain_db=10.0), 48000, 2, -10.0)     # asks for far more than fits
    assert seg.max_dBFS <= -0.9


def test_unreadable_sound_file(tmp_path):
    bad = tmp_path / "A.mp3"
    bad.write_bytes(b"not audio")
    with pytest.raises(RuntimeError, match="can't read A.mp3"):
        load_sound(bad, SoundOptions(), 48000, 2, -16.0)
