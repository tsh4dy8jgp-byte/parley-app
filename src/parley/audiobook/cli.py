"""Command-line interface: text file + language -> MP3 audiobook."""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

from ..mp3tags import add_tag_args, read_cover_error, tags_from_args
from .builder import build_audiobook, print_progress
from .chunker import DEFAULT_CHUNK_SIZE
from .voices import is_multilingual, list_voices, multilingual_warning, resolve_voice

SOUND_CUE = re.compile(r"^\s*\[sound\s+\w[^\]]*\]\s*$", re.M | re.I)   # Parley's insert lines


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="audiobook",
        description="Create an MP3 audiobook from a large text using Edge TTS.",
    )
    parser.add_argument(
        "-i", "--input",
        help="Path to a UTF-8 text file. Use '-' (or omit) to read from stdin.",
        default="-",
    )
    parser.add_argument(
        "-l", "--language",
        help="Language code, e.g. en, en-GB, ru, kk, zh. Required unless --voice is given.",
    )
    parser.add_argument(
        "-o", "--output",
        default="audiobook.mp3",
        help="Output MP3 path (default: %(default)s).",
    )
    parser.add_argument(
        "--voice",
        help="Exact Edge voice short name (e.g. en-US-GuyNeural). Overrides --language.",
    )
    parser.add_argument(
        "--gender",
        choices=["Male", "Female"],
        help="Preferred narrator gender when picking a voice by language.",
    )
    parser.add_argument("--rate", default="+0%", help="Speech rate, e.g. -10%% or +25%%.")
    parser.add_argument("--volume", default="+0%", help="Volume, e.g. +10%%.")
    parser.add_argument("--pitch", default="+0Hz", help="Pitch, e.g. -5Hz.")
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=DEFAULT_CHUNK_SIZE,
        help="Max characters per TTS request (default: %(default)s).",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=4,
        help="Parallel TTS requests (default: %(default)s).",
    )
    add_tag_args(parser)
    parser.add_argument(
        "--list-voices",
        action="store_true",
        help="List available voices (optionally filtered by --language) and exit.",
    )
    return parser


def read_text(source: str) -> str:
    if source == "-":
        if sys.stdin.isatty():
            print("Reading text from stdin; finish with Ctrl-D.", file=sys.stderr)
        return sys.stdin.read()
    path = Path(source)
    if not path.is_file():
        raise SystemExit(f"error: input file not found: {path}")
    return path.read_text(encoding="utf-8")


async def run(args: argparse.Namespace) -> None:
    if args.list_voices:
        for voice in await list_voices(args.language):
            print(f"{voice['ShortName']:<40} {voice['Gender']:<8} {voice['Locale']}")
        return

    if not args.voice and not args.language:
        raise SystemExit("error: provide --language (e.g. -l en) or an explicit --voice")

    tags = tags_from_args(args)
    if problem := read_cover_error(tags):
        raise SystemExit(f"error: {problem}")
    text = read_text(args.input)
    if not text.strip():
        raise SystemExit("error: input text is empty")

    if SOUND_CUE.search(text):
        print("warning: the text has [sound Name] lines, which this tool reads aloud as text. To insert "
              "the audio files, open the text in the Parley app or render a tagged script with dialog-tts.",
              file=sys.stderr)

    voice = args.voice or await resolve_voice(args.language, args.gender)
    print(f"Narrator voice: {voice}", file=sys.stderr)
    if is_multilingual(voice):
        print(multilingual_warning("the narrator", voice), file=sys.stderr)

    output = await build_audiobook(
        text,
        voice,
        Path(args.output),
        rate=args.rate,
        volume=args.volume,
        pitch=args.pitch,
        max_chars=args.chunk_size,
        concurrency=args.concurrency,
        tags=tags,
        on_progress=print_progress,
    )
    size_mb = output.stat().st_size / (1024 * 1024)
    print(f"Audiobook written to {output} ({size_mb:.1f} MB)", file=sys.stderr)


def main() -> None:
    args = build_parser().parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        raise SystemExit(130)


if __name__ == "__main__":
    main()
