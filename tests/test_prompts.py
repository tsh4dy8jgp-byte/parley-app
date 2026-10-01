import re
from pathlib import Path

import pytest

from parley.dialog_tts.script import parse_text
from parley.dialog_tts.voices import duplicate_voice_warnings

PROMPTS = sorted((Path(__file__).resolve().parents[1] / "prompts").glob("*.md"))
SCRIPTS = [(p.name, m.group(1)) for p in PROMPTS
           for m in re.finditer(r"^```text\n(.*?)^```$", p.read_text(encoding="utf-8"), re.S | re.M)]


def test_prompts_have_script_examples():
    assert SCRIPTS, "no ```text script examples found in prompts/"


@pytest.mark.parametrize("name, text", SCRIPTS, ids=[n for n, _ in SCRIPTS])
def test_prompt_script_examples_parse_without_warnings(name, text):
    s = parse_text(text, name)
    warnings = duplicate_voice_warnings(s.voices, (u.role for u in s.utterances))
    assert not [w for w in warnings if w.startswith("warning")]
