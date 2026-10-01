from parley.textfiles import load, markdown_to_speech as md, read_text


def test_headings_become_their_own_paragraph():
    assert md("# Chapter 1\nIt was dark.\n## Part *one* ##\nRain.") == "Chapter 1\n\nIt was dark.\n\nPart one\n\nRain."


def test_inline_formatting_is_removed():
    assert md("Some **bold**, __strong__, *it*, _em_, ~~old~~ and `code`.") == \
        "Some bold, strong, it, em, old and code."


def test_symbols_that_are_not_formatting_survive():
    assert md("snake_case_name and 2 * 3 * 4 and a_b") == "snake_case_name and 2 * 3 * 4 and a_b"
    assert md(r"An \*escaped\* star and \# hash") == "An *escaped* star and # hash"


def test_links_images_and_footnotes():
    text = ("See [the docs](https://x.io \"t\") and [ref][1].![logo](a.png) Visit <https://x.io>.[^2]\n\n"
            "[1]: https://example.com\n[^2]: A footnote.")
    assert md(text) == "See the docs and ref. Visit .\n\nA footnote."


def test_lists_quotes_and_tasks():
    text = "- one\n* two\n+ three\n1. first\n2) second\n- [ ] todo\n- [x] done\n> quoted\n>> nested"
    assert md(text) == "one\ntwo\nthree\nfirst\nsecond\ntodo\ndone\nquoted\nnested"


def test_code_blocks_rules_html_and_front_matter_are_dropped():
    text = ("---\ntitle: Book\n---\nIntro.\n\n```python\nprint('x')\n```\n~~~\nraw\n~~~\n***\n"
            "<div align=\"center\">Centered</div>\n<!-- hidden\nnote -->\nEnd.")
    assert md(text) == "Intro.\n\nCentered\n\nEnd."


def test_tables_become_comma_separated_rows():
    text = "| Name | Age |\n|:-----|----:|\n| Anna | 30 |\n| Ben | 25 |"
    assert md(text) == "Name, Age\nAnna, 30\nBen, 25"


def test_load_joins_in_natural_name_order(tmp_path):
    for name, body in [("ch10.md", "# Ten"), ("ch2.txt", "Two."), ("ch1.md", "**One.**")]:
        (tmp_path / name).write_text(body, encoding="utf-8")
    got = load([tmp_path / "ch10.md", tmp_path / "ch2.txt", tmp_path / "ch1.md"])
    assert got.text == "One.\n\nTwo.\n\nTen"
    assert [p.name for p in got.used] == ["ch1.md", "ch2.txt", "ch10.md"]
    assert [p.name for p in got.cleaned] == ["ch1.md", "ch10.md"]


def test_load_skips_unsupported_and_missing(tmp_path):
    (tmp_path / "a.txt").write_text("Hello.", encoding="utf-8")
    (tmp_path / "b.png").write_bytes(b"\x89PNG")
    (tmp_path / "folder.md").mkdir()
    got = load([tmp_path / "b.png", tmp_path / "a.txt", tmp_path / "missing.md", tmp_path / "folder.md"])
    assert got.text == "Hello."
    assert sorted(p.name for p in got.skipped) == ["b.png", "folder.md", "missing.md"]
    assert "skipped b.png, folder.md, missing.md" in got.summary()


def test_load_keeps_tagged_dialogue_markdown_as_is(tmp_path):
    script = "# Cast: Anna = A\n[A] **Hi** there.\n[B] Hello."
    (tmp_path / "talk.md").write_text(script, encoding="utf-8")
    got = load([tmp_path / "talk.md"])
    assert got.text == script and not got.cleaned


def test_read_text_encodings(tmp_path):
    (tmp_path / "bom.txt").write_bytes("﻿Grüß".encode("utf-8"))
    (tmp_path / "win.txt").write_bytes("café – naïve".encode("cp1252"))
    (tmp_path / "odd.txt").write_bytes(b"x\x81y")              # not UTF-8, not cp1252
    assert read_text(tmp_path / "bom.txt") == "Grüß"
    assert read_text(tmp_path / "win.txt") == "café – naïve"
    assert read_text(tmp_path / "odd.txt") == "x\x81y"


def test_summary_wording(tmp_path):
    (tmp_path / "a.md").write_text("# A", encoding="utf-8")
    (tmp_path / "b.txt").write_text("B", encoding="utf-8")
    assert load([tmp_path / "a.md"]).summary() == "Opened a.md · Markdown formatting removed"
    assert load([tmp_path / "a.md", tmp_path / "b.txt"]).summary() == \
        "Opened 2 files (a.md … b.txt) · Markdown formatting removed"


def test_dialogue_example_inside_a_code_block_does_not_count(tmp_path):
    doc = "# Format\n\nWrite **one** line per turn:\n\n```text\n[A] Hello!\n[B] Hi.\n```\n"
    (tmp_path / "readme.md").write_text(doc, encoding="utf-8")
    got = load([tmp_path / "readme.md"])
    assert got.cleaned and got.text == "Format\n\nWrite one line per turn:"
