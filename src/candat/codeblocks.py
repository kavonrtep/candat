"""Readable raw markdown: fenced code blocks and inline spans.

Textual's TextArea highlights markdown with the block grammar only — a
fenced block is one opaque node and inline markup (code spans, emphasis,
links) is not parsed at all. This module fills those gaps for the editor:

- `fenced_blocks` finds the fenced code blocks (CommonMark closing rule: same
  fence character, at least as long as the opener);
- `block_highlights` parses a block's content with the grammar named by its
  info string (```python, ```r, Rmd's ```{r, echo=FALSE}) and returns
  highlights in TextArea's own (row, start_byte, end_byte, name) shape;
- `inline_highlights` styles code spans, bold, italic and links on a prose
  line with regexes.

No Textual widgets here; grammars come from Textual's builtins or the same
on-demand registry the editor uses (R, config formats).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache, lru_cache

from .markdown import FENCE_RE

# Info-string aliases -> editor language names.
ALIASES: dict[str, str] = {
    "py": "python",
    "python3": "python",
    "ipython": "python",
    "pycon": "python",
    "sh": "bash",
    "shell": "bash",
    "zsh": "bash",
    "console": "bash",
    "shell-session": "bash",
    "js": "javascript",
    "jsx": "javascript",
    "yml": "yaml",
    "htm": "html",
    "rs": "rust",
    "golang": "go",
    "jsonc": "json",
    "dockerfile": "dockerfile",
    "docker": "dockerfile",
    "makefile": "make",
    "splus": "r",
    "rscript": "r",
}


@dataclass
class Fence:
    """A fenced code block: `open` and `close` are the delimiter rows
    (`close` is None when the block runs to the end of the document)."""

    open: int
    close: int | None
    info: str

    @property
    def language(self) -> str | None:
        return fence_language(self.info)

    def content_rows(self, line_count: int) -> range:
        end = self.close if self.close is not None else line_count
        return range(self.open + 1, end)

    def contains(self, row: int) -> bool:
        """Whether `row` is inside the block, delimiters included."""
        return self.open <= row and (self.close is None or row <= self.close)


def fence_language(info: str) -> str | None:
    """The language named by a fence's info string, normalised: ```Python,
    ```{r, echo=FALSE} and ```py title="x" all work."""
    word = (
        re.split(r"[\s,{}]+", info.strip().strip("{}").strip())[0]
        if info.strip()
        else ""
    )
    word = word.lower().lstrip(".")
    if not word:
        return None
    return ALIASES.get(word, word)


def fenced_blocks(lines: list[str]) -> list[Fence]:
    blocks: list[Fence] = []
    opener: tuple[int, str, str] | None = None  # (row, fence, info)
    for row, line in enumerate(lines):
        m = FENCE_RE.match(line)
        if m is None:
            continue
        fence = m["fence"]
        rest = line[m.end() :]
        if opener is None:
            # A backtick fence's info string may not contain backticks.
            if fence[0] == "`" and "`" in rest:
                continue
            opener = (row, fence, rest.strip())
        elif (
            fence[0] == opener[1][0]
            and len(fence) >= len(opener[1])
            and not rest.strip()
        ):
            blocks.append(Fence(opener[0], row, opener[2]))
            opener = None
    if opener is not None:
        blocks.append(Fence(opener[0], None, opener[2]))
    return blocks


def fence_at(lines: list[str], row: int) -> Fence | None:
    for block in fenced_blocks(lines):
        if block.contains(row):
            return block
        if block.open > row:
            break
    return None


# -- injected highlighting --------------------------------------------------

Highlight = tuple[int, int, "int | None", str]  # row, start byte, end byte, name


@cache
def _grammar(language: str):
    """(tree_sitter.Language, Query) for an editor language name, or None."""
    try:
        from textual._tree_sitter import get_language
        from textual.widgets._text_area import TextArea
        from tree_sitter import Query, QueryError
    except ImportError:
        return None
    from .editor import extra_language

    grammar, query = None, ""
    if (extra := extra_language(language)) is not None:
        grammar, query = extra
    else:
        grammar = get_language(language)
        if grammar is not None:
            query = TextArea._get_builtin_highlight_query(language)
    if grammar is None or not query:
        return None
    try:
        return grammar, Query(grammar, query)
    except QueryError:  # a query this grammar version rejects
        return None


@lru_cache(maxsize=256)
def _parse_block(language: str, source: str) -> tuple[Highlight, ...]:
    grammar = _grammar(language)
    if grammar is None:
        return ()
    from tree_sitter import Parser, QueryCursor

    language_obj, query = grammar
    tree = Parser(language_obj).parse(source.encode("utf-8"))
    out: list[Highlight] = []
    for name, nodes in QueryCursor(query).captures(tree.root_node).items():
        for node in nodes:
            (r0, c0), (r1, c1) = node.start_point, node.end_point
            if r0 == r1:
                out.append((r0, c0, c1, name))
                continue
            out.append((r0, c0, None, name))
            out.extend((r, 0, None, name) for r in range(r0 + 1, r1))
            out.append((r1, 0, c1, name))
    return tuple(out)


def block_highlights(block: Fence, lines: list[str]) -> list[Highlight]:
    """Syntax highlights for a block's content in document rows, plus the
    fence line styling. Unknown languages get only the fence styling."""
    out: list[Highlight] = []
    for row in (block.open, block.close):
        if row is None:
            continue
        line = lines[row]
        m = FENCE_RE.match(line)
        out.append((row, 0, None, "fence.delimiter"))
        if row == block.open and block.info:
            start = len(line[: m.end()].encode()) if m else 0
            out.append((row, start, None, "fence.info"))
    language = block.language
    rows = block.content_rows(len(lines))
    if language is None or not rows:
        return out
    source = "\n".join(lines[rows.start : rows.stop]) + "\n"
    for row, start, end, name in _parse_block(language, source):
        out.append((rows.start + row, start, end, name))
    return out


# -- inline spans -------------------------------------------------------------

_CODE_SPAN = re.compile(r"(`+)(?!`)(.+?)(?<!`)\1(?!`)")
_BOLD = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1")
_ITALIC = re.compile(
    r"(?<![*\w])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?!\*)"
    r"|(?<![_\w])_(?=\S)(.+?)(?<=\S)_(?![_\w])"
)
_LINK = re.compile(r"!?\[(?P<label>[^\]\n]+)\]\((?P<url>[^)\s]+)(?:\s+\"[^\"]*\")?\)")
_AUTOLINK = re.compile(r"<https?://[^>\s]+>|(?<![(<])\bhttps?://[^\s)>\]]+")


def inline_highlights(line: str) -> list[tuple[int, int, str]]:
    """(start_byte, end_byte, name) spans for one prose line. Emphasis and
    links inside code spans are left alone."""
    if not any(c in line for c in "`*_[<h"):
        return []
    spans: list[tuple[int, int, str]] = []
    code: list[tuple[int, int]] = []
    for m in _CODE_SPAN.finditer(line):
        code.append(m.span())
        spans.append((m.start(), m.end(), "inline_code"))

    def free(start: int, end: int) -> bool:
        return not any(s < end and start < e for s, e in code)

    for m in _BOLD.finditer(line):
        if free(*m.span()):
            spans.append((m.start(), m.end(), "bold"))
    for m in _ITALIC.finditer(line):
        if free(*m.span()):
            spans.append((m.start(), m.end(), "italic"))
    for m in _LINK.finditer(line):
        if free(*m.span()):
            spans.append((m.start("label"), m.end("label"), "link.label"))
            spans.append((m.start("url"), m.end("url"), "link.uri"))
    for m in _AUTOLINK.finditer(line):
        if free(*m.span()):
            spans.append((m.start(), m.end(), "link.uri"))
    if line.isascii():
        return spans
    return [(len(line[:s].encode()), len(line[:e].encode()), n) for s, e, n in spans]
