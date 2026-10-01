"""Opening text files: .txt / .md, several at once, Markdown turned into speech-ready text."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Set, Union

from .jobs import detect

SUPPORTED = (".txt", ".md", ".markdown")
MARKDOWN = (".md", ".markdown")
FILETYPES = [("Text and Markdown", "*.txt *.md *.markdown"), ("All files", "*")]


@dataclass
class Loaded:
    text: str = ""
    used: List[Path] = field(default_factory=list)      # files whose text is in `text`, in order
    cleaned: List[Path] = field(default_factory=list)   # Markdown files that were converted
    skipped: List[Path] = field(default_factory=list)   # unsupported, missing or unreadable

    def summary(self) -> str:
        if not self.used:
            msg = "Nothing opened: only .txt and .md files are supported"
        elif len(self.used) == 1:
            msg = f"Opened {self.used[0].name}"
        else:
            msg = f"Opened {len(self.used)} files ({self.used[0].name} … {self.used[-1].name})"
        if self.cleaned:
            msg += " · Markdown formatting removed"
        if self.skipped:
            msg += " · skipped " + ", ".join(p.name for p in self.skipped)
        return msg


def load(paths: Iterable[Union[str, Path]]) -> Loaded:
    """Read the supported files in natural name order (ch2 before ch10) and join them."""
    out = Loaded()
    parts = []
    for path in sorted((Path(p).expanduser() for p in paths), key=_natural):
        if path.suffix.lower() not in SUPPORTED or not path.is_file():
            out.skipped.append(path)
            continue
        try:
            text = read_text(path)
        except OSError:
            out.skipped.append(path)
            continue
        # A tagged dialogue saved as .md is a script, not prose: its '# Cast:' line must stay a comment.
        # Tags inside code blocks are examples in a document and don't count.
        if path.suffix.lower() in MARKDOWN and detect(_FENCED_BLOCK.sub("", text)).mode != "dialogue":
            text = markdown_to_speech(text)
            out.cleaned.append(path)
        out.used.append(path)
        if text.strip():
            parts.append(text.strip())
    out.text = "\n\n".join(parts)
    return out


def read_text(path: Union[str, Path]) -> str:
    """UTF-8 (with or without BOM), else Windows-1252, else Latin-1 (never fails)."""
    data = Path(path).read_bytes()
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            pass
    return data.decode("latin-1")


def _natural(path: Path):
    return [int(t) if t.isdigit() else t.casefold() for t in re.split(r"(\d+)", path.name)]


# --- Markdown -> speech ----------------------------------------------------------------------------

_ESCAPED = re.compile(r"\\([\\`*_{}\[\]()#+\-.!<>|~])")
_PROTECT = 0xE000                    # escaped characters are parked in the private-use area
_FRONT_MATTER = re.compile(r"\A---\n.*?\n(?:---|\.\.\.)\n", re.S)
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_FENCE = re.compile(r"^(```|~~~)")
_FENCED_BLOCK = re.compile(r"^[ \t]*(```|~~~).*?^[ \t]*\1[^\n]*$", re.S | re.M)
_RULE = re.compile(r"^([-*_=])(\s*\1){2,}$")
_TABLE_SEP = re.compile(r"^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?$")
_HEADING = re.compile(r"^#{1,6}\s+(.*?)(?:\s+#+)?$")
_QUOTE = re.compile(r"^(>\s?)+")
_LIST = re.compile(r"^([-*+]|\d{1,9}[.)])\s+(\[[ xX]\]\s+)?")
_FOOTNOTE_DEF = re.compile(r"^\[\^[^\]]+\]:\s*")
_REF_DEF = re.compile(r"^\[[^\]]+\]:\s*\S")
_INLINE = [
    (re.compile(r"!\[[^\]]*\](\((?:[^()]|\([^)]*\))*\)|\[[^\]]*\])"), ""),    # images
    (re.compile(r"\[([^\]]+)\]\((?:[^()]|\([^)]*\))*\)"), r"\1"),              # [text](url "title")
    (re.compile(r"\[([^\]]+)\]\[[^\]]*\]"), r"\1"),                            # [text][ref]
    (re.compile(r"\[\^[^\]]+\]"), ""),                                         # footnote markers
    (re.compile(r"</?[A-Za-z][^>\n]*>"), ""),                                  # HTML tags, <autolinks>
    (re.compile(r"`+([^`]+?)`+"), r"\1"),                                      # `code`
    (re.compile(r"(?<!\w)(\*\*|__)(?!\s)(.+?)(?<!\s)\1(?!\w)"), r"\2"),        # **bold** __bold__
    (re.compile(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])"), r"\1"),         # *italic*
    (re.compile(r"(?<![\w_])_(?!\s)(.+?)(?<!\s)_(?![\w_])"), r"\1"),           # _italic_
    (re.compile(r"~~(.+?)~~"), r"\1"),                                         # ~~strike~~
]


def markdown_to_speech(markdown: str) -> str:
    """Text a voice can read: Markdown syntax removed, headings as their own paragraphs."""
    text = markdown.replace("\r\n", "\n").replace("\r", "\n")
    text = _ESCAPED.sub(lambda m: chr(_PROTECT + ord(m.group(1))), text)
    text = _FRONT_MATTER.sub("", text)
    text = _COMMENT.sub("", text)
    lines = text.split("\n")
    tables = _table_rows(lines)
    out: List[str] = []
    fence = None
    for i, raw in enumerate(lines):
        s = raw.strip()
        if fence:
            if s.startswith(fence):
                fence = None
            continue
        m = _FENCE.match(s)
        if m:
            fence = m.group(1)
            out.append("")
            continue
        if i in tables:
            if not _TABLE_SEP.match(s):
                out.append(", ".join(_inline(c.strip()) for c in s.strip("|").split("|")))
            continue
        if _RULE.match(s):
            out.append("")
            continue
        m = _HEADING.match(s)
        if m:
            out += ["", _inline(m.group(1)), ""]
            continue
        s = _LIST.sub("", _QUOTE.sub("", s))
        if _FOOTNOTE_DEF.match(s):
            s = _FOOTNOTE_DEF.sub("", s)
        elif _REF_DEF.match(s):
            continue
        out.append(_inline(s))
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(line.strip() for line in out)).strip()
    return re.sub("[-]", lambda m: chr(ord(m.group()) - _PROTECT), text)


def _inline(s: str) -> str:
    for pattern, repl in _INLINE:
        s = pattern.sub(repl, s)
    return s


def _table_rows(lines: List[str]) -> Set[int]:
    """Indices of lines that belong to a pipe table (a separator row plus the rows around it)."""
    rows: Set[int] = set()
    for i, line in enumerate(lines):
        if "|" in line and "-" in line and _TABLE_SEP.match(line.strip()):
            rows.add(i)
            for step in (-1, 1):
                j = i + step
                while 0 <= j < len(lines) and "|" in lines[j] and lines[j].strip():
                    rows.add(j)
                    j += step
    return rows
