import struct
import sys
import tkinter as tk
from pathlib import Path

import pytest

pytest.importorskip("customtkinter")

from parley import app as ui  # noqa: E402
from parley import settings as st  # noqa: E402
from parley.catalog import Catalog  # noqa: E402

from test_parley import RAW  # noqa: E402


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(st, "DRAFT_FILE", tmp_path / "draft.txt")
    monkeypatch.setattr(ui.cat, "load_cached", lambda _path: Catalog.from_edge(RAW))
    monkeypatch.setattr(ui.messagebox, "askyesno", lambda *a, **k: True)
    try:
        a = ui.ParleyApp(fetch_catalog=False)
    except tk.TclError as e:
        pytest.skip(f"no display: {e}")
    a.withdraw()
    a.update()
    yield a
    try:
        a.destroy()
    except tk.TclError:  # already closed by the test
        pass


def test_window_is_named_parley_and_has_its_icon(app):
    assert app.title() == "Parley"
    pngs = ui.ico_pngs(ui.ICON.read_bytes())
    widths = [struct.unpack(">I", png[16:20])[0] for png in pngs]   # PNG IHDR width
    assert widths == [256, 128, 64, 48, 32, 24, 16]
    if sys.platform != "win32":   # Windows reads the .ico itself
        assert len(app._icons) == len(pngs)


def test_example_dialogue_is_detected_and_cast(app):
    app.insert_example()
    app.update()
    assert app.badge.cget("text").strip() == "Dialogue · 3 speakers"
    assert app.advanced._shown == ["A", "B", "C"]
    assert app.advanced.rows["A"].picker.menu.get().startswith("Automatic · ")
    assert "A → " in app.standard.status.cget("text")


def test_multilingual_narrator_is_flagged_in_the_status_line(app):
    app.model.narrator.voice.set("en-US-AndrewMultilingualNeural")
    app.standard.set_text("Just some prose to read aloud.")
    app.validate_now()
    assert "⚠ the narrator uses multilingual voice" in app.standard.status.cget("text")


def test_problem_lines_are_marked(app):
    app.standard.set_text("[A] Hi\n[B Yo\n[C] Bye")
    app.validate_now()
    assert app.badge.cget("text").strip() == "1 problem"
    assert [str(i) for i in app.standard.editor._textbox.tag_ranges("problem")] == ["2.0", "3.0"]


def test_explicit_voice_and_language_switch(app):
    app.insert_example()
    app.update()
    app.advanced.rows["B"].picker._pick("Guy · M")
    app.validate_now()
    assert app.check.voices["B"].voice == "en-US-GuyNeural"
    app.model["language"].set("kk-KZ")
    app.validate_now()
    assert app.check.voices["A"].voice.startswith("kk-KZ")
    assert app.check.voices["B"].voice == "en-US-GuyNeural"        # explicit choice is kept


def test_guide_tabs_and_empty_generate(app):
    was_open = app.model["guide_open"].get()
    app.toggle_guide()
    assert app.model["guide_open"].get() != was_open
    assert bool(app.guide.grid_info()) != was_open
    app.tabs.set("Advanced")
    app.tabs.set("Standard")
    app.clear_text()
    app.generate()
    assert "Nothing to read" in app.status.cget("text")
    assert not app.gen.busy


def test_close_saves_settings_and_draft(app, tmp_path):
    app.standard.set_text("Hello world.")
    app.model["srt"].set(True)
    app.on_close()
    assert (tmp_path / "draft.txt").read_text(encoding="utf-8") == "Hello world."
    assert st.load(tmp_path / "settings.json").srt is True


class DropEvent:
    def __init__(self, data):
        self.data = data


def test_drop_joins_files_and_cleans_markdown(app, tmp_path):
    folder = tmp_path / "my books"
    folder.mkdir()
    (folder / "chapter 2.md").write_text("# Two\nSome **bold** text.", encoding="utf-8")
    (folder / "chapter 1.txt").write_text("One.", encoding="utf-8")
    app._drop(DropEvent(f"{{{folder / 'chapter 2.md'}}} {{{folder / 'chapter 1.txt'}}}"))
    assert app.standard.text() == "One.\n\nTwo\n\nSome bold text."
    assert app.model["out_name"].get() == "chapter 1"
    assert app.source == folder / "chapter 1.txt" and app._save_as is None   # joined text: Save… asks a new name
    assert "Opened 2 files" in app.status.cget("text") and "Markdown formatting removed" in app.status.cget("text")


def test_drop_rejects_unsupported_files(app, tmp_path):
    (tmp_path / "photo.png").write_bytes(b"\x89PNG")
    app.standard.set_text("keep me")
    app._drop(DropEvent(str(tmp_path / "photo.png")))
    assert app.standard.text() == "keep me"
    assert app.status.cget("text").startswith("Nothing opened") and "skipped photo.png" in app.status.cget("text")


def test_single_txt_keeps_its_name_for_save(app, tmp_path):
    (tmp_path / "talk.txt").write_text("[A] Hi\n[B] Yo", encoding="utf-8")
    app.open_paths([tmp_path / "talk.txt"])
    assert app._save_as == tmp_path / "talk.txt"
    assert app.badge.cget("text").strip() == "Dialogue · 2 speakers"


def test_drop_target_and_hint(app):
    pytest.importorskip("tkinterdnd2")
    assert app.dnd is True
    app._drop_enter(None)
    assert app.standard.drop_hint.winfo_manager() == "place"
    app._drop(DropEvent(""))
    assert app.standard.drop_hint.winfo_manager() == ""


LESSON = Path(__file__).resolve().parent / "fixtures" / "lektion_02.tagged.txt"


def test_advanced_changes_reach_the_parsed_lesson(app):
    app.open_paths([LESSON])
    app.advanced.rows["Male2"].vars.rate.set(30)
    app.advanced.rows["Male3"].picker._pick(app.advanced.rows["Male3"].picker.menu.cget("values")[1])
    app.validate_now()
    assert app.check.voices["Male2"].rate == "+30%"
    assert app.check.voices["Male3"].voice != "de-DE-KillianNeural"
    assert app.advanced.rows["Male3"].note.cget("text") == "replaces the text's @voices"
    assert app.advanced.rows["Narrator"].note.cget("text") == "voice from the text's @voices"
    assert "not used for this dialogue" in app.advanced.narrator_caption.cget("text")


GERMAN = [{"ShortName": n, "Gender": g, "Locale": loc, "LocaleName": label}
          for n, g, loc, label in [("de-DE-KatjaNeural", "Female", "de-DE", "German (Germany)"),
                                   ("de-DE-ConradNeural", "Male", "de-DE", "German (Germany)"),
                                   ("de-DE-KillianNeural", "Male", "de-DE", "German (Germany)"),
                                   ("de-CH-JanNeural", "Male", "de-CH", "German (Switzerland)")]]


@pytest.fixture
def de_app(app):
    app.catalog = Catalog.from_edge(RAW + GERMAN)
    app.standard.set_languages(app.catalog)
    return app


def language_state(app):
    std = app.standard
    note = std.language_note.cget("text") if std.language_row.winfo_manager() else ""
    fix = std.language_fix.cget("text") if std.language_fix.winfo_manager() else ""
    return std.language.cget("state"), std.language.get(), note, fix


def test_language_shows_detected_when_text_sets_every_voice(de_app):
    de_app.open_paths([LESSON])
    state, shown, note, fix = language_state(de_app)
    assert (state, shown, fix) == ("disabled", "Detected from text · German", "")
    assert "set in the text (@voices)" in note
    assert de_app.standard.gender.cget("state") == "disabled"
    assert de_app.model["language"].get() == "en-US"                 # the user's choice is untouched
    de_app.standard.set_text("Plain prose again.")
    de_app.validate_now()
    assert language_state(de_app) == ("normal", "English (United States)", "", "")


def test_language_mismatch_offers_one_click_fix(de_app):
    de_app.standard.set_text("@voices\nA = de-DE-KatjaNeural\n@end\n[A] Hallo!\n[B] Servus!")
    de_app.validate_now()
    state, shown, note, fix = language_state(de_app)
    assert (state, shown, fix) == ("normal", "English (United States)", "Use German (Germany)")
    assert note == "The text's voices are German, but B gets automatic English (United States) voices."
    de_app.standard.language_fix.invoke()
    assert de_app.model["language"].get() == "de-DE"
    assert language_state(de_app) == ("normal", "German (Germany)", "", "")
    assert de_app.check.voices["B"].voice.startswith("de-")


def test_sounds_card_lists_found_and_missing_sounds(app, tmp_path, monkeypatch):
    from conftest import write_sound

    (tmp_path / "story.txt").write_text("It was late.\n[sound Rain]\n[sound Bell]\nThe end.", encoding="utf-8")
    write_sound(tmp_path / "sounds", "Rain.wav")
    app.open_paths([tmp_path / "story.txt"])
    assert app.badge.cget("text").strip() == "1 problem"                  # Bell is missing
    texts = [[c.cget("text") for c in row.winfo_children()] for row in app.advanced.sound_rows]
    assert texts[0][0] == "Rain" and texts[0][2].startswith("Rain.wav · 0:02")
    assert texts[1][0] == "Bell" and texts[1][2] == "✕ put Bell.mp3 (or .wav, .m4a, .ogg, .flac) in the sounds folder"
    assert str(tmp_path / "sounds") in app.advanced.sounds_note.cget("text")
    write_sound(tmp_path / "sounds", "Bell.wav")
    app.validate_now()                                                   # what ↻ Check again does
    assert app.badge.cget("text").strip() == "Narration · 2 sounds"
    assert "· 2 sounds ·" in app.standard.status.cget("text")
    assert [str(i) for i in app.standard.editor._textbox.tag_ranges("speaker")] == ["2.0", "2.12", "3.0", "3.12"]
    opened = []
    monkeypatch.setattr(ui.system, "open_path", opened.append)
    app.advanced.open_sound_folder()
    assert opened == [tmp_path / "sounds"]
    app.standard.set_text("Plain prose.")
    app.validate_now()
    assert app.advanced.sound_rows == [] and app.advanced.no_sounds.winfo_manager() == "grid"
