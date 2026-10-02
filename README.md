# Parley

*From one narrator to a full cast.*

Parley turns text into speech with [edge-tts](https://pypi.org/project/edge-tts/)
(Microsoft Edge neural voices — free, no API key required). It comes in three parts:

- **The Parley desktop app** (`parley`): open or drop text and generate. Plain text becomes a
  single-voice audiobook, and text with speaker tags (`[A] Hello!`) becomes a multi-voice dialogue.
- **`audiobook`**: a command line tool that turns a large text + a language code into one MP3.
- **`dialog-tts`**: a command line tool that renders role-tagged dialogue scripts into multi-voice
  MP3s with subtitles, shadowing and slow versions.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[gui]"     # the desktop app; `pip install -e .` is enough for the audiobook command
```

Dialogues also need ffmpeg on PATH (`brew install ffmpeg`, `sudo apt install ffmpeg` or
`winget install Gyan.FFmpeg`). The ready-made app below has it built in.

## Ready-made app (macOS, Windows)

`packaging/build.py` turns the desktop app into a program that needs no Python and has ffmpeg
built in:

- **macOS**: `Parley-<version>-macos-arm64.dmg` (`-macos-x64` for Intel Macs). Open it and drag
  Parley to Applications.
- **Windows**: `Parley-<version>-windows-x64.zip`. Unzip it anywhere and run `Parley\Parley.exe`.
  Keep the folder together.

Builds are unsigned unless the maintainer has set up the signing secrets below (then they are signed,
and on macOS notarized, and open without a warning). If a build is unsigned, the first start needs one extra step.
On macOS, open Parley, close the warning, then go to System Settings → Privacy & Security and
click *Open Anyway*. On Windows, click *More info* → *Run anyway* in the SmartScreen window.

To sign the CI builds, add these repository secrets (Settings → Secrets and variables → Actions);
each platform signs only if its secrets exist:

- **macOS** (needs an Apple Developer Program membership, $99/year): `MACOS_CERT_P12` (base64 of a
  "Developer ID Application" certificate exported as .p12), `MACOS_CERT_PASSWORD`,
  `APPLE_SIGN_IDENTITY` (e.g. `Developer ID Application: Your Name (TEAMID)`), `APPLE_ID`,
  `APPLE_TEAM_ID`, `APPLE_APP_PASSWORD` (app-specific password for notarization).
- **Windows** (needs a code-signing certificate from a CA): `WIN_CERT_PFX` (base64 of the .pfx) and
  `WIN_CERT_PASSWORD`. SmartScreen reputation builds up with downloads for standard certificates;
  EV certificates are trusted at once.

To build it:

```bash
pip install -e ".[gui,build]"
python packaging/build.py            # downloads ffmpeg once, builds, self-tests, writes dist/Parley-…
python packaging/build.py --online   # the self-test also synthesizes one line (needs network)
```

PyInstaller only builds for the system it runs on. `.github/workflows/build.yml` builds for
Windows, Apple Silicon and Intel Macs on GitHub. Run it from the Actions tab, or push a tag like
`v0.1.0` to attach the three files to a release. `--ffmpeg-dir DIR` bundles your own portable
ffmpeg and ffprobe instead of downloading them. A built app can check itself without opening a
window: `Parley.app/Contents/MacOS/Parley --self-test report.txt` (Windows:
`Parley.exe --self-test report.txt`).

The app runs FFmpeg as a separate, unmodified program. The macOS build of FFmpeg is GPLv3 and the
Windows build is LGPL. Their licenses and source links are in the app's `ffmpeg` folder
(`NOTICE.txt`, `LICENSE.txt`).

## Desktop app (`parley.py`)

The Parley window covers both tools. Plain text becomes a single-voice audiobook. Text with
speaker tags (`[A] Hello!`) becomes a multi-voice dialogue. The app detects which one you have.

```bash
pip install -e ".[gui]"             # customtkinter, tkinterdnd2, pydub; dialogues also need ffmpeg
python parley.py                    # or: parley
```

- **Opening text**: drop `.txt` or `.md` files anywhere on the window, or use Open…. Several files are
  joined in name order (`chapter_2` before `chapter_10`). Markdown is turned into readable text:
  headings become their own paragraph, and formatting marks, links, images, code blocks and HTML
  are removed. A `.md` file that is already a tagged dialogue is opened as it is.
- **Standard tab**: language, narrator voice (Auto / Female / Male), the text editor and where to save.
  The line under the editor checks the text as you type. It shows the speakers and their voices,
  or any problems with their line number, and problem lines are tinted. It also warns (⚠) when the
  narrator or a speaker uses a `*Multilingual*` voice.
- **Advanced tab**: a voice and speed / pitch / volume per speaker (▶ previews it), dialogue gaps,
  extra outputs (SRT, LRC, shadowing, slow version, clips, WAV), loudness, lexicon file,
  parallel requests, the cache, and Light / Dark theme.
- **Guide** (button or F1): how to write a dialogue for speakers A, B and C, with
  *Insert example dialogue* (English, German, French, Spanish or Russian, following the language).
- Shortcuts: ⌘/Ctrl+Enter generate, Esc cancel, ⌘/Ctrl+O open, ⌘/Ctrl+S save text.

Settings and the editor draft are kept in `~/.config/parley/`. Synthesised lines are cached in
`~/.cache/parley/` (Advanced → Performance shows the size and has a button to clear it).
Files written by the CLI work in the app and the other way round. Voices are decided in this order,
lowest to highest: automatic for the chosen language < the file's `@voices` block < what you
change in Advanced → Voices. A speaker you leave on Automatic keeps the file's voice, and the speed,
pitch and volume sliders are added on top of whichever voice is used. (The CLI has no Advanced tab,
so there the file's block wins as before.)

Parley used to be called TTS Studio. On its first start it moves your settings, draft and cache from
`~/.config/tts-studio/` and `~/.cache/tts-studio/` to the new folders. Your output folder is not
changed, because your files are there; pick a new one on the Standard tab if you like.

## Audiobooks (`audiobook`)

Turn a large text + a language code into an MP3 audiobook.

### How it works

1. The text is split into ~3000-character chunks on paragraph/sentence
   boundaries (`chunker.py`), so huge inputs work and prosody stays natural.
2. The language code is resolved to a neural voice (`voices.py`) — curated
   defaults for common languages, live catalogue lookup for everything else.
3. Chunks are synthesized concurrently with retries (`builder.py`) and
   concatenated in order into a single MP3.

### Usage

```bash
# From a text file
audiobook -i book.txt -l en -o book.mp3

# From stdin
cat chapter1.txt | audiobook -l ru -o chapter1.mp3

# Pick narrator gender, tweak speed
audiobook -i book.txt -l en --gender Male --rate -10% -o book.mp3

# Use an exact voice
audiobook -i book.txt --voice en-GB-RyanNeural -o book.mp3

# See available voices for a language
audiobook --list-voices -l es
```

Language codes: `en`, `en-GB`, `es`, `fr`, `de`, `ru`, `kk`, `zh`, `ja`, … —
anything the Edge voice catalogue supports.

### Python API

```python
import asyncio
from pathlib import Path
from parley.audiobook import build_audiobook
from parley.audiobook.voices import resolve_voice

async def make():
    voice = await resolve_voice("en")
    await build_audiobook(open("book.txt").read(), voice, Path("book.mp3"))

asyncio.run(make())
```

### Notes

- Requires an internet connection (audio is generated by Microsoft's service).
- Output is MP3 (24 kHz, 48 kbit/s mono). Edge TTS emits self-contained MP3
  streams, so chunk outputs are concatenated directly without re-encoding.
- Tune `--concurrency` down if you hit throttling on very large books.
- `*Multilingual*` voices guess the language of each line, so their pronunciation may be messy,
  especially on short lines. `audiobook` prints a warning when the narrator is one.

## Multi-voice dialogues (`dialog_tts.py`)

`dialog-tts` renders role-tagged dialogue files into MP3s where each role has its own edge-tts
voice. It also writes subtitles, a shadowing version and a slow version. Dialogues need ffmpeg on PATH.
To have a chat assistant write a script with you, paste the interview prompt in
[prompts/spoken-piece-interview.md](prompts/spoken-piece-interview.md) into a new chat. It asks about
style, cast, voices and accents first, then writes a script in this format.

```bash
pip install -e ".[dialog]"          # plus ffmpeg on PATH

# check a file without calling TTS: prints index, line, role, voice, prosody, text
python dialog_tts.py render dialogue.tagged.txt --dry-run

# render with subtitles, a shadowing version and a slow version
python dialog_tts.py render dialogue.tagged.txt -o out --srt --shadow --slow

# several files (the glob is expanded by the tool, so quote it)
python dialog_tts.py render "lessons/*.tagged.txt" -o out --srt

# list all voices (or one locale: --locale en-GB), or check that every voice a file uses
# exists (needs network)
python dialog_tts.py voices dialogue.tagged.txt
```

`python dialog_tts.py …` works without installing; after `pip install -e .` the same commands are
available as `dialog-tts …`.

### Outputs (for `dialogue.tagged.txt`)

The output name is cut at the first dot, so `dialogue.tagged.txt` becomes `dialogue`.

| file | flag | content |
|---|---|---|
| `dialogue.mp3` | always | the dialogue, one voice per role |
| `dialogue.srt` / `.lrc` | `--srt` / `--lrc` | one cue per line: `Waiter: Good evening!` |
| `dialogue.shadow.mp3` (+ `.srt`/`.lrc`) | `--shadow` | after each dialogue line, silence of 1.3 × its length (`--shadow-factor`) to repeat it aloud. Narrator lines get none. |
| `dialogue.slow.mp3` (+ `.srt`/`.lrc`) | `--slow` | every line re-synthesised with rate −20 % added to the role's own rate (not time-stretched) |
| `dialogue_clips/NNN_Role.mp3` | `--clips` | each line trimmed and loudness-matched |
| `.wav` next to each `.mp3` | `--wav` | uncompressed copy |

### Options

| option | default | meaning |
|---|---|---|
| `-o, --out DIR` | `out` | output folder |
| `--voices FILE` | – | voices.yaml with role defaults (see below) |
| `--lexicon FILE` | `lexicon.txt` next to each file | lexicon of `word = spoken form` lines |
| `--continue-speaker` | off | untagged lines continue the previous speaker, instead of being an error |
| `--gap-change MS` | 450 | silence between lines of different speakers |
| `--gap-same MS` | 250 | silence between two lines of the same speaker |
| `--gap-repeat MS` | 700 | silence between repetitions of a `repeat=N` line |
| `--loudness lufs\|dbfs` | lufs | how voices are matched: EBU R128 or plain dBFS |
| `--target X` | −16 LUFS / −20 dBFS | loudness target |
| `--concurrency N` | 4 | parallel TTS requests |
| `--cache-dir DIR` | `.dialog_tts_cache` | cache of synthesised lines |
| `--album TEXT` | `Parley` | MP3 album tag |

A `--voices` file maps roles to a voice name, or to a voice with prosody:

```yaml
Narrator: en-US-AndrewNeural
A: {voice: en-GB-RyanNeural, rate: "-5%", pitch: "-2Hz"}
```

### File format

```text
# Cast: Waiter = A; Guest = B
@voices
Narrator = en-US-AndrewNeural   rate=-10%
A        = en-GB-RyanNeural
B        = en-US-AvaNeural
@end
@lexicon
Worcestershire = Wooster-sheer
@end

[Narrator] At the restaurant.
[pause 1.5]
[A] Good evening! A table for one?
[B] Yes, please. Is the {soup|soop} good today?
[B slow repeat=2] Yes, please.
[A] It comes with Worcestershire sauce.
```

| element | rule |
|---|---|
| `# …` | comment. `# Cast: Name = Role; …` also sets the speaker labels in the subtitles. |
| blank line | ignored |
| `@voices … @end` | `Role = voice [rate=±N%] [pitch=±NHz] [volume=±N%]`. Roles missing here must come from `--voices`. There is no built-in cast, so an unmapped role is reported as an error. |
| `@lexicon … @end` | `word = spoken form`. Every whole-word occurrence is sent to TTS respelled, but the subtitles keep the original spelling. A `lexicon.txt` next to the file applies too; the file's own entries win. |
| `[Role] text` | one utterance. Role names are voice slots (`A`, `Male1`, `Narrator` …), not characters. |
| `[Role mod …] text` | line modifiers: `slow` (rate −20 %), `repeat=N` (1–9, the same clip N times, 700 ms apart), and the raw overrides `rate=±N%`, `pitch=±NHz`, `volume=±N%` (added to the role's values). |
| `{shown\|spoken}` | inline respelling for one occurrence. |
| `[pause N]` | N seconds of silence, alone on its line. It replaces the normal turn gap. |

The parser reports **every** problem with its line number before any TTS request is made:
untagged text, a role missing from the voice map, an empty utterance, an unknown control tag or
modifier, bad prosody values, a malformed `@voices` line, and an unclosed block. It also warns when
two roles in one file would sound identical, and when a role uses a `*Multilingual*` voice. Those
guess the language of each line, so their pronunciation may be messy, especially on short lines.

## Acknowledgements

Parley is a thin layer over other people's work.

- **[edge-tts](https://github.com/rany2/edge-tts)** by rany2 does all the speech synthesis. It
  talks to the online service behind Microsoft Edge's Read Aloud and lists its voices. Parley would
  not exist without it.
- **Microsoft** makes the neural voices. Parley is not affiliated with or endorsed by Microsoft. The
  voices come from an online service that Microsoft can change or switch off at any time.
- **[Martin Riedl](https://ffmpeg.martin-riedl.de)** (macOS) and
  **[BtbN](https://github.com/BtbN/FFmpeg-Builds)** (Windows) publish the portable FFmpeg builds
  that the ready-made apps bundle.

Parley is built on these projects. Each keeps its own license.

| project | used for | license |
|---|---|---|
| [edge-tts](https://github.com/rany2/edge-tts) | speech synthesis and the voice list | LGPL-3.0 |
| [FFmpeg](https://ffmpeg.org), with [LAME](https://lame.sourceforge.io) for MP3 | decoding, loudness measurement and encoding of dialogue audio | GPL-3.0 (macOS build), LGPL-2.1+ (Windows build) |
| [pydub](https://github.com/jiaaro/pydub) | cutting, joining and exporting audio (through FFmpeg) | MIT |
| [audioop-lts](https://github.com/AbstractUmbra/audioop) | the `audioop` module pydub needs on Python 3.13+ | PSF-2.0 |
| [PyYAML](https://pyyaml.org) | `--voices` files | MIT |
| [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter) by Tom Schimansky | the desktop app's widgets and Light / Dark themes | MIT |
| [tkinterdnd2](https://github.com/Eliav2/tkinterdnd2), wrapping [tkdnd](https://github.com/petasis/tkdnd) by Georgios Petasis | dropping files on the window | MIT, Tcl-style BSD |
| [PyInstaller](https://pyinstaller.org) and [pyinstaller-hooks-contrib](https://github.com/pyinstaller/pyinstaller-hooks-contrib) | building the ready-made app | GPL-2.0+ with a bootloader exception; Apache-2.0 / GPL-2.0 |
| [Pillow](https://python-pillow.org) | turning the `.ico` into `.icns` at build time | MIT-CMU |
| [pytest](https://docs.pytest.org) | the tests | MIT |

The ready-made apps also contain Python and Tcl/Tk, under their own licenses.

## References

The documents and standards the design follows:

- **Voices and prosody.** Microsoft's [voice list](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support?tabs=tts)
  explains the voice names (`en-GB-RyanNeural`). Its [prosody docs](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/speech-synthesis-markup-voice#adjust-prosody)
  explain `rate`, `pitch` and `volume`. The service accepts one voice with these three values per
  request and no custom SSML ([edge-tts on custom SSML](https://github.com/rany2/edge-tts#custom-ssml)),
  which is why the file format has no emphasis or break tags.
- **Loudness.** `--loudness lufs` matches voices by loudness in LUFS, as defined in
  [ITU-R BS.1770](https://www.itu.int/rec/R-REC-BS.1770) and [EBU R 128](https://tech.ebu.ch/publications/r128),
  and measures it with FFmpeg's [`ebur128` filter](https://ffmpeg.org/ffmpeg-filters.html#ebur128).
  The −16 LUFS default and the −1 dB peak ceiling follow [AES TD1004](https://www.aes.org/technical/documents/AESTD1004_1_15_10.pdf),
  the AES recommendation for streamed and downloaded audio.
- **Shadowing.** The `--shadow` version leaves room to repeat each line aloud. The exercise is named
  after [speech shadowing](https://en.wikipedia.org/wiki/Speech_shadowing).
- **Subtitles.** [SubRip (`.srt`)](https://en.wikipedia.org/wiki/SubRip) and
  [LRC (`.lrc`)](https://en.wikipedia.org/wiki/LRC_(file_format)).
