"""dialog-tts: render role-tagged dialogue files into multi-voice MP3s."""

from __future__ import annotations

import argparse
import asyncio
import glob
import sys
import time
from pathlib import Path
from typing import List

from .render import RenderOptions, render_scripts
from .script import Script, ScriptError, load_lexicon, parse_file
from .stitch import Gaps
from .tts import CachedBackend, EdgeBackend
from .voices import DEFAULT_VOICES, check_voices_exist, duplicate_voice_warnings, load_voices_yaml


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dialog-tts", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("render", help="render tagged files to audio")
    r.add_argument("files", nargs="+", help="tagged .txt files; globs are expanded (also on Windows)")
    r.add_argument("-o", "--out", default="out", help="output folder (default: %(default)s)")
    r.add_argument("--voices", help="voices.yaml with role -> voice defaults (a file's @voices wins)")
    r.add_argument("--lexicon", help="course lexicon 'word = spoken form' (default: lexicon.txt next to "
                   "each tagged file, if present; a file's @lexicon entries win)")
    r.add_argument("--dry-run", action="store_true", help="print the parsed segment table, no TTS")
    r.add_argument("--continue-speaker", action="store_true",
                   help="treat untagged lines as more text from the previous speaker")
    r.add_argument("--srt", action="store_true", help="write .srt subtitles (speaker: text)")
    r.add_argument("--lrc", action="store_true", help="write .lrc lyrics-style subtitles")
    r.add_argument("--shadow", action="store_true",
                   help="also write NAME.shadow.mp3 with repeat-after-me silence after each dialogue line")
    r.add_argument("--shadow-factor", type=float, default=1.3, help="shadow silence = factor x line (%(default)s)")
    r.add_argument("--slow", action="store_true", help="also write NAME.slow.mp3, re-synthesised at rate -20%%")
    r.add_argument("--clips", action="store_true", help="keep per-line clips in NAME_clips/")
    r.add_argument("--wav", action="store_true", help="also export WAV")
    r.add_argument("--gap-change", type=int, default=450, help="ms between different speakers (%(default)s)")
    r.add_argument("--gap-same", type=int, default=250, help="ms between lines of one speaker (%(default)s)")
    r.add_argument("--gap-repeat", type=int, default=700, help="ms between repetitions (%(default)s)")
    r.add_argument("--loudness", choices=["lufs", "dbfs"], default="lufs",
                   help="equalise voices by EBU R128 loudness or by plain dBFS (%(default)s)")
    r.add_argument("--target", type=float, help="loudness target (default -16 LUFS / -20 dBFS)")
    r.add_argument("--concurrency", type=int, default=4, help="parallel TTS requests (%(default)s)")
    r.add_argument("--cache-dir", default=".dialog_tts_cache", help="TTS cache folder (%(default)s)")

    v = sub.add_parser("voices", help="list German edge-tts voices and check files' voice mapping")
    v.add_argument("files", nargs="*", help="tagged files to check")
    v.add_argument("--locale", default="de-", help="voice name prefix to list (%(default)s)")
    return p


def expand(patterns: List[str]) -> List[Path]:
    out: List[Path] = []
    for pat in patterns:
        hits = sorted(glob.glob(pat)) if any(ch in pat for ch in "*?[") else [pat]
        if not hits:
            raise SystemExit(f"error: no files match {pat}")
        out += [Path(h) for h in hits]
    return out


def load_scripts(files: List[Path], voices_yaml, continue_speaker: bool, lexicon=None) -> List[Script]:
    base = dict(DEFAULT_VOICES)
    if voices_yaml:
        base.update(load_voices_yaml(Path(voices_yaml)))
    lexicons = {}
    scripts, failed = [], False
    for f in files:
        try:
            lex_path = Path(lexicon) if lexicon else Path(f).parent / "lexicon.txt"
            if lex_path not in lexicons:
                lexicons[lex_path] = load_lexicon(lex_path) if (lexicon or lex_path.is_file()) else {}
            s = parse_file(f, base_voices=base, base_lexicon=lexicons[lex_path],
                           continue_speaker=continue_speaker)
        except FileNotFoundError:
            print(f"error: file not found: {f}", file=sys.stderr)
            failed = True
            continue
        except ScriptError as e:
            print(e, file=sys.stderr)
            failed = True
            continue
        for w in duplicate_voice_warnings(s.voices, (u.role for u in s.utterances)):
            print(f"{f}: {w}", file=sys.stderr)
        scripts.append(s)
    if failed:
        raise SystemExit(2)
    return scripts


def print_table(s: Script) -> None:
    print(f"== {s.path}")
    print(f"{'#':>3} {'line':>4}  {'role':<9} {'voice':<34} {'prosody':<24} text")
    i = 0
    for seg in s.segments:
        if hasattr(seg, "seconds"):
            print(f"{'':>3} {seg.line:>4}  {'[pause]':<9} {'':<34} {'':<24} {seg.seconds:g} s")
            continue
        i += 1
        extra = f" x{seg.repeat}" if seg.repeat > 1 else ""
        text = seg.text if seg.speak == seg.text else f"{seg.text}   (says: {seg.speak})"
        print(f"{i:>3} {seg.line:>4}  {seg.role:<9} {seg.spec.voice:<34} "
              f"{seg.spec.describe() + extra:<24} {text}")


def _progress(done: int, total: int) -> None:
    sys.stderr.write(f"\r  synthesising {done}/{total}")
    if done == total:
        sys.stderr.write("\n")
    sys.stderr.flush()


def cmd_render(a: argparse.Namespace) -> None:
    scripts = load_scripts(expand(a.files), a.voices, a.continue_speaker, a.lexicon)
    if a.dry_run:
        for s in scripts:
            print_table(s)
        return
    target = a.target if a.target is not None else (-16.0 if a.loudness == "lufs" else -20.0)
    opts = RenderOptions(
        gaps=Gaps(change_ms=a.gap_change, same_ms=a.gap_same, repeat_ms=a.gap_repeat),
        shadow_factor=a.shadow_factor, shadow=a.shadow, slow=a.slow, srt=a.srt, lrc=a.lrc,
        clips=a.clips, wav=a.wav, loudness=a.loudness, target=target,
    )
    backend = CachedBackend(EdgeBackend(concurrency=a.concurrency), Path(a.cache_dir))
    t0 = time.perf_counter()
    written = asyncio.run(render_scripts(scripts, backend, Path(a.out), opts, _progress))
    elapsed = time.perf_counter() - t0
    for path, ms in written:
        dur = f"{ms / 1000:7.1f} s" if path.suffix == ".mp3" else ""
        print(f"  {path}  {dur}")
    print(f"{len(scripts)} file(s), {backend.misses} lines synthesised, {backend.hits} from cache, "
          f"{elapsed:.1f} s", file=sys.stderr)


def cmd_voices(a: argparse.Namespace) -> None:
    import edge_tts

    voices = [v for v in asyncio.run(edge_tts.list_voices()) if v["ShortName"].startswith(a.locale)]
    for v in sorted(voices, key=lambda v: v["ShortName"]):
        print(f"{v['ShortName']:<36} {v['Gender']:<7} {v['Locale']}")
    if a.files:
        scripts = load_scripts(expand(a.files), None, False)
        specs = {spec for s in scripts for spec in s.voices.values()}
        missing = asyncio.run(check_voices_exist(specs))
        print("all mapped voices exist" if not missing else f"MISSING voices: {', '.join(missing)}")
        if missing:
            raise SystemExit(1)


def main(argv=None) -> None:
    a = build_parser().parse_args(argv)
    try:
        {"render": cmd_render, "voices": cmd_voices}[a.cmd](a)
    except KeyboardInterrupt:
        raise SystemExit(130)


if __name__ == "__main__":
    main()
