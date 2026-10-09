"""ID3 tags for the MP3s Parley writes, so music apps can sort and show them.

Pure Python (mutagen), no Tk and no ffmpeg: both engines use it.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional

from mutagen.id3 import APIC, COMM, ID3, TALB, TCON, TDRC, TIT2, TPE1, TPE2, TRCK, ID3NoHeaderError

DEFAULT_ARTIST = "Parley"
DEFAULT_ALBUM = "Parley"
DEFAULT_GENRE = "Speech"
COVER_EXTENSIONS = (".jpg", ".jpeg", ".png")


@dataclass(frozen=True)
class Tags:
    """Empty fields are left out. `title` empty means: the caller's default (the file name)."""

    title: str = ""
    artist: str = DEFAULT_ARTIST
    album: str = DEFAULT_ALBUM
    album_artist: str = ""          # empty: music apps group by artist
    genre: str = DEFAULT_GENRE
    year: str = ""
    track: str = ""                 # "3" or "3/12"
    comment: str = ""
    cover: str = ""                 # path to a .jpg / .png image

    def with_title(self, default: str, suffix: str = "") -> "Tags":
        """Title for one output file: the user's title plus *suffix* (" (slow)"), else *default*."""
        title = self.title.strip()
        return replace(self, title=title + suffix if title else default)


def _mime(data: bytes) -> str:
    return "image/png" if data.startswith(b"\x89PNG") else "image/jpeg"


def write_tags(path: Path, tags: Tags) -> None:
    """Replace the ID3 tag of the MP3 at *path* (ID3v2.3, which every player reads)."""
    try:
        id3 = ID3(path)
        id3.delete()
        id3 = ID3()
    except ID3NoHeaderError:
        id3 = ID3()
    text = lambda s: s.strip()  # noqa: E731
    for frame, value in ((TIT2, tags.title), (TPE1, tags.artist), (TALB, tags.album), (TPE2, tags.album_artist),
                         (TCON, tags.genre), (TDRC, tags.year), (TRCK, tags.track)):
        if text(value):
            id3.add(frame(encoding=3, text=text(value)))
    if text(tags.comment):
        id3.add(COMM(encoding=3, lang="eng", desc="", text=text(tags.comment)))
    if tags.cover:
        data = Path(tags.cover).expanduser().read_bytes()     # OSError: reported to the user
        id3.add(APIC(encoding=3, mime=_mime(data), type=3, desc="Cover", data=data))
    id3.save(path, v2_version=3)


# --- command line options shared by `audiobook` and `dialog-tts` -----------------------------------

def add_tag_args(parser: argparse.ArgumentParser, title: bool = True) -> None:
    g = parser.add_argument_group("MP3 tags")
    if title:
        g.add_argument("--title", default="", help="title (default: the file name)")
    g.add_argument("--artist", default=DEFAULT_ARTIST, help="artist (%(default)s)")
    g.add_argument("--album", default=DEFAULT_ALBUM, help="album (%(default)s)")
    g.add_argument("--album-artist", default="", help="album artist (default: none)")
    g.add_argument("--genre", default=DEFAULT_GENRE, help="genre (%(default)s)")
    g.add_argument("--year", default="", help="year, e.g. 2025")
    g.add_argument("--track", default="", help="track number, e.g. 3 or 3/12")
    g.add_argument("--comment", default="", help="comment")
    g.add_argument("--cover", default="", help="cover image (.jpg or .png)")


def tags_from_args(a: argparse.Namespace) -> Tags:
    return Tags(title=getattr(a, "title", ""), artist=a.artist, album=a.album, album_artist=a.album_artist,
                genre=a.genre, year=a.year, track=a.track, comment=a.comment, cover=a.cover)


def read_cover_error(tags: Tags) -> Optional[str]:
    """A message if the cover file is unusable, so callers can fail before a long synthesis."""
    if not tags.cover:
        return None
    p = Path(tags.cover).expanduser()
    if not p.is_file():
        return f"cover image not found: {p}"
    if p.suffix.lower() not in COVER_EXTENSIONS:
        return "cover image must be a .jpg or .png file"
    return None
