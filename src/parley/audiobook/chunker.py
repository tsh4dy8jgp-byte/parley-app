"""Split large texts into TTS-friendly chunks.

The Edge TTS service works best with requests of a few thousand characters.
Splitting on paragraph and sentence boundaries keeps prosody natural and
lets failed chunks be retried without redoing the whole book.
"""

from __future__ import annotations

import re
from typing import List

DEFAULT_CHUNK_SIZE = 3000

_SENTENCE_END = re.compile(r"(?<=[.!?。！？…])\s+")


def split_text(text: str, max_chars: int = DEFAULT_CHUNK_SIZE) -> List[str]:
    """Split *text* into chunks of at most *max_chars* characters.

    Prefers paragraph boundaries, then sentence boundaries, then hard cuts
    for pathological runs with no punctuation at all.
    """
    if max_chars < 1:
        raise ValueError("max_chars must be positive")

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: List[str] = []
    current = ""

    for para in paragraphs:
        pieces = [para] if len(para) <= max_chars else _split_paragraph(para, max_chars)
        for piece in pieces:
            if not current:
                current = piece
            elif len(current) + len(piece) + 2 <= max_chars:
                current = f"{current}\n\n{piece}"
            else:
                chunks.append(current)
                current = piece

    if current:
        chunks.append(current)
    return chunks


def _split_paragraph(para: str, max_chars: int) -> List[str]:
    sentences = _SENTENCE_END.split(para)
    pieces: List[str] = []
    current = ""

    for sentence in sentences:
        parts = [sentence] if len(sentence) <= max_chars else _hard_split(sentence, max_chars)
        for part in parts:
            if not current:
                current = part
            elif len(current) + len(part) + 1 <= max_chars:
                current = f"{current} {part}"
            else:
                pieces.append(current)
                current = part

    if current:
        pieces.append(current)
    return pieces


def _hard_split(text: str, max_chars: int) -> List[str]:
    words = text.split()
    pieces: List[str] = []
    current = ""

    for word in words:
        while len(word) > max_chars:  # a single "word" longer than the limit
            pieces.append(word[:max_chars])
            word = word[max_chars:]
        if not current:
            current = word
        elif len(current) + len(word) + 1 <= max_chars:
            current = f"{current} {word}"
        else:
            pieces.append(current)
            current = word

    if current:
        pieces.append(current)
    return pieces
