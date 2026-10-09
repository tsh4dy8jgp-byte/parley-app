"""Read a text file whatever its encoding: no Tk, no engine imports, so every module can use it."""

from __future__ import annotations

import codecs
from pathlib import Path
from typing import Union


def decode(data: bytes) -> str:
    """UTF-8 (with or without BOM), UTF-16 with a BOM (Windows "Unicode"), else Windows-1252,
    else Latin-1 (never fails). Umlauts, accents and other scripts survive all of them."""
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        try:
            return data.decode("utf-16")
        except UnicodeDecodeError:
            pass
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            pass
    return data.decode("latin-1")


def read_text(path: Union[str, Path]) -> str:
    return decode(Path(path).read_bytes())
