# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**Parley** — *from one narrator to a full cast.*

Parley turns text into speech with [edge-tts](https://pypi.org/project/edge-tts/) (Microsoft Edge neural voices, free, needs network). The project (`pyproject.toml` name `parley`) is one package, `src/parley`, with two engines as subpackages:

- **`parley`** is the Parley desktop app (customtkinter). It detects whether the text is narration or dialogue and calls the matching engine.
- **`parley.audiobook`** turns plain text into a single-voice MP3. It needs only edge-tts, not ffmpeg. CLI: `audiobook`.
- **`parley.dialog_tts`** renders role-tagged dialogue scripts (`[Role] text`) into multi-voice MP3s with subtitles and shadowing/slow variants. It needs pydub and ffmpeg. CLI: `dialog-tts`.

The engines never import the app. `parley/__init__.py` is only a docstring, so importing `parley.dialog_tts` or `parley.audiobook` does not load Tk, and both CLIs work without the `gui` extra. Inside `parley`, modules import the engines relatively (`from .dialog_tts.script import ...`). Tests import them as `parley.dialog_tts.*`.

The desktop app was called TTS Studio before. The old name now appears only in the migration code (`settings.migrate_legacy` and its tests), which moves the old config and cache folders, and in a note in the README.

The German course "Deutsch im Ohr" (lesson files, the `.src` → tagged converter, the prosody study) lives outside this repo. Book `.txt` inputs in the repo root and all `.mp3`/`.wav` outputs are gitignored. Keep them out of the repo.

## Commands

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dialog,gui,dev]"            # ffmpeg must also be on PATH (brew install ffmpeg)

python -m pytest -q                           # all tests (no network; ffmpeg required)
python -m pytest tests/test_script.py -q      # one file
python -m pytest tests/test_stitch.py -k shadow -q   # one test by name

# Run without installing (root launchers add src/ to sys.path)
python dialog_tts.py render tests/fixtures/lektion_02.tagged.txt --dry-run   # parse only, prints segment table
python dialog_tts.py render dialogue.tagged.txt -o out --srt --shadow --slow
python dialog_tts.py render "lessons/*.tagged.txt" -o out --srt   # quote the glob; the tool expands it
python dialog_tts.py voices dialogue.tagged.txt                   # check mapped voices exist (network)
python parley.py                                                  # Parley desktop app (installed: parley)
audiobook -i book.txt -l en -o book.mp3                           # after pip install -e .
```

There is no linter config. `--dry-run` is the fastest way to check a tagged file, because the parser reports every problem before any TTS call.

The root launcher `parley.py` has the same name as the package. From the repo root, `python -c "import parley"` picks up the launcher, not the package, unless `src/` comes first on `sys.path`. The launchers and `tests/conftest.py` put it first.

## Architecture

### dialog_tts pipeline (`parley/dialog_tts/render.py` ties it together)

1. **Parse** (`script.py`). `parse_text` collects *all* problems and raises one `ScriptError` with line numbers. Every `Utterance` has both `text`, which goes in the subtitles, and `speak`, which goes to TTS after the `@lexicon` / `lexicon.txt` / inline `{shown|spoken}` respellings. Keep the two separate.
2. **Synthesize** (`tts.py`). Every unique `(speak, VoiceSpec)` pair across all scripts is synthesized once by `synth_all`. `CachedBackend` wraps `EdgeBackend`. The cache file is `<cache_dir>/<sha256 of text+voice+rate+pitch+volume>.mp3` and is written atomically. The `--slow` variant is re-synthesized at rate −20%, not time-stretched.
3. **Stitch** (`stitch.py`). Clips are decoded to 24 kHz mono 16-bit and trimmed of edge-tts padding. `role_gains` gives each role one gain toward −16 LUFS. `loudness.py` measures with ffmpeg `ebur128`. `plan_timeline` is pure: it takes only segment durations and produces both the audio layout and the subtitle cues, so subtitles match the audio exactly. `assemble` joins raw PCM, and `finalize` applies one gain capped at a −1 dBFS peak.
4. **Output**: MP3, plus optional WAV/SRT/LRC and a per-line clips folder. The output stem is cut at the first dot, so `lektion_02.tagged.txt` becomes `lektion_02`.

`Backend` is a Protocol (`fmt` + `async synth`). Tests use `FakeBackend` in `tests/conftest.py`, which returns WAV sine tones of 40 ms per character with a different loudness per voice.

### Voice resolution and precedence

- `DEFAULT_VOICES` in `src/parley/dialog_tts/voices.py` is the cast of the German course. The course's lesson files carry the same cast in their `@voices` blocks (its converter generates them from this list), so a change here should be mirrored there. `tests/fixtures/lektion_02.tagged.txt` is a copy of lesson 2 that the UI tests open; update it too.
- CLI order, lowest to highest: `DEFAULT_VOICES` < `--voices` yaml < the file's `@voices` block.
- GUI order, lowest to highest: German course defaults or `Catalog.auto_cast` < the file's `@voices` < the Advanced tab. The Advanced tab is applied through the `adjust_voice` hook of `parse_text` (`jobs.speaker_adjuster`).
- A `lexicon.txt` next to a tagged file applies automatically. A file's own `@lexicon` entries win over it.

### Parley desktop app (`parley`)

- `jobs.py` holds all logic that does not need a display: text detection, validation, casting, and running the engines. Tests cover it without Tk. `detect()` reuses the private `_TAG_LINE` / `_ROLE_NAME` regexes from `dialog_tts.script`, so a change to the tag syntax also changes how the GUI detects dialogue.
- `Runner` runs one coroutine on its own thread with its own event loop and posts `Event`s to a queue. Only the Tk main thread touches widgets, by polling that queue (`app._poll`).
- `settings.Settings` is a single dataclass persisted as JSON. `_coerce` drops values with the wrong type or an unknown choice. `model.Model` mirrors it as Tk variables. Files go in `~/.config/parley/` (override with the `PARLEY_HOME` env var), and the line cache is in `~/.cache/parley/`. On startup, `app.main()` calls `settings.migrate_legacy()` (unless `PARLEY_HOME` is set). It moves the TTS Studio folders over and never overwrites existing Parley ones. The tests construct `ParleyApp` directly, so they never trigger it.
- `catalog.py` loads the voice list from a cached copy of the last online fetch, or from a built-in offline fallback.
- The window icon is `src/parley/parley.ico`, declared as package data in `pyproject.toml`. Windows loads the `.ico` directly. Elsewhere, `ico_pngs` extracts the PNG images inside it for `iconphoto`.
- UI tests (`test_parley_ui.py`) are skipped when customtkinter or a display is missing.

### audiobook

`chunker.split_text` splits text into chunks of about 3000 characters, on paragraph, then sentence, then word boundaries. `builder` synthesizes the chunks concurrently with retries and concatenates the MP3 bytes directly without re-encoding.

## Constraints

These come from measurements made for the German course (its `README.md` and `PROSODY_REPORT.md`, outside this repo).

- **edge-tts accepts no custom SSML.** Each request takes one voice plus rate, pitch and volume. There is no in-sentence emphasis, `<break>` or phonemes. `[emph]`, `fast`, `soft` and `loud` were deliberately left out after measurement. Do not add them back.
- **Avoid `*Multilingual*` voices for German.** They guess the language of each line and mispronounce short lines. `duplicate_voice_warnings` flags them, along with roles that would sound identical.
- Only two de-AT voices exist (Jonas, Ingrid). The other roles reuse de-DE voices and are told apart by prosody shifts.
- On Python 3.13+, pydub needs `audioop-lts`, which the `dialog` and `gui` extras install.
