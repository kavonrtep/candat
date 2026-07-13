"""Markdown editing smarts: pure text transforms used by the editor.

Everything here works on plain strings and lists of lines (no Textual
imports) so each behaviour is unit-testable without a running app:

- smart Enter: list/quote marker continuation (`continuation`,
  `is_empty_item`), code-fence auto-close (`unclosed_fence_opener`)
- M-q reformatting: paragraph/list/quote fill and pipe-table alignment
  (`reformat`, `fill_paragraph`, `align_table`)
- ordered-list renumbering (`renumber`) and list indent/outdent
  (`indent_item`, `outdent_item`)
- table cell geometry for Tab navigation (`cell_bounds`, `cell_index`)
- the small helpers: checkbox toggle, emphasis toggle, word/URL detection

Fenced code blocks are sacred: callers check `in_fence` first (and
`reformat` checks it itself), so nothing here reflows code.

Deliberate simplifications, documented rather than solved: a table row must
start with `|`; pipes inside inline code spans are not special (escaped
`\\|` is honoured); fences indented more than 3 spaces are not recognised.
"""

from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass

from rich.cells import cell_len

# A list item: indent, bullet or ordered marker, optional task box.
ITEM_RE = re.compile(
    r"^(?P<indent>\s*)"
    r"(?P<marker>[-*+]|\d{1,9}[.)])"
    r"(?P<space>\s+)"
    r"(?P<box>\[[ xX]\]\s+)?"
    r"(?P<content>.*)$"
)
# One or more '>' quote markers, each optionally followed by one space.
QUOTE_RE = re.compile(r"^(?P<prefix>\s{0,3}(?:>[ ]?)+)(?P<rest>.*)$")
FENCE_RE = re.compile(r"^(?P<indent>\s{0,3})(?P<fence>`{3,}|~{3,})")
HEADING_RE = re.compile(r"^\s{0,3}#{1,6}(\s|$)")
# Thematic breaks and setext-heading underlines: never fill through these.
RULE_RE = re.compile(r"\s{0,3}(=+|-+|\*{3,}|_{3,})\s*")
ORDERED_RE = re.compile(r"^(?P<number>\d{1,9})(?P<delim>[.)])$")
_CELL_SPLIT = re.compile(r"(?<!\\)\|")
_BOX_RE = re.compile(r"\[([ xX])\]")
_WORD_RE = re.compile(r"\w+")
_URL_RE = re.compile(r"https?://\S+")


@dataclass
class Item:
    """A parsed list-item line. `quote` is any blockquote prefix before it."""

    quote: str
    indent: str
    marker: str  # '-', '*', '+', '1.', '1)'
    space: str  # whitespace between marker and box/content
    box: str | None  # '[ ] ' / '[x] ' including trailing space, or None
    content: str

    @property
    def head(self) -> str:
        """Everything before the content."""
        return self.quote + self.indent + self.marker + self.space + (self.box or "")


def parse_item(line: str) -> Item | None:
    quote, rest = "", line
    if (q := QUOTE_RE.match(line)) is not None:
        quote, rest = q["prefix"], q["rest"]
    m = ITEM_RE.match(rest)
    if m is None:
        return None
    return Item(quote, m["indent"], m["marker"], m["space"], m["box"], m["content"])


def _kind(line: str) -> str:
    """Coarse block classification of one line (ignoring fence state)."""
    if not line.strip():
        return "blank"
    if FENCE_RE.match(line):
        return "fence"
    if HEADING_RE.match(line):
        return "heading"
    if RULE_RE.fullmatch(line):
        return "rule"
    if is_table_row(line):
        return "table"
    if parse_item(line) is not None:
        return "item"
    if QUOTE_RE.match(line) is not None:
        return "quote"
    return "text"


def in_fence(lines: list[str], row: int) -> bool:
    """Whether `row` sits inside a fenced code block (or on its closing
    line): the parity of fence delimiters strictly above it."""
    count = sum(1 for line in lines[:row] if FENCE_RE.match(line))
    return count % 2 == 1


def content_col(line: str) -> int:
    """Column where the line's content starts, past any quote/list prefix."""
    item = parse_item(line)
    if item is not None:
        return len(item.head)
    if (q := QUOTE_RE.match(line)) is not None:
        return len(q["prefix"])
    return 0


# -- smart Enter ---------------------------------------------------------


def continuation(line: str) -> str | None:
    """The prefix a new line should start with when Enter is pressed at the
    end of `line`, or None when the line doesn't continue anything."""
    item = parse_item(line)
    if item is not None:
        marker = item.marker
        if (m := ORDERED_RE.match(marker)) is not None:
            marker = f"{int(m['number']) + 1}{m['delim']}"
        box = "[ ] " if item.box is not None else ""
        return item.quote + item.indent + marker + item.space + box
    if (q := QUOTE_RE.match(line)) is not None:
        return q["prefix"]
    return None


def is_empty_item(line: str) -> bool:
    """A list/quote line with a marker but no content — Enter should end
    the list rather than continue it."""
    item = parse_item(line)
    if item is not None:
        if item.box is not None:
            return item.content.strip() == ""
        content = item.content.strip()
        return content == "" or _BOX_RE.fullmatch(content) is not None
    if (q := QUOTE_RE.match(line)) is not None:
        return q["rest"].strip() == ""
    return False


def unclosed_fence_opener(lines: list[str], row: int) -> str | None:
    """When `row` opens a code fence that nothing closes, the closing
    delimiter to insert below; else None."""
    m = FENCE_RE.match(lines[row])
    if m is None or in_fence(lines, row):
        return None
    total = sum(1 for line in lines if FENCE_RE.match(line))
    if total % 2 == 0:
        return None
    return m["indent"] + m["fence"]


# -- tables ----------------------------------------------------------------


def is_table_row(line: str) -> bool:
    return line.strip().startswith("|")


def split_cells(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    return [cell.strip() for cell in _CELL_SPLIT.split(s)]


def is_separator_row(line: str) -> bool:
    cells = split_cells(line)
    return bool(cells) and all(re.fullmatch(r":?-+:?", cell) for cell in cells)


def table_extent(lines: list[str], row: int) -> tuple[int, int] | None:
    """First and last row of the contiguous table around `row`."""
    if not is_table_row(lines[row]):
        return None
    start = row
    while start > 0 and is_table_row(lines[start - 1]):
        start -= 1
    end = row
    while end + 1 < len(lines) and is_table_row(lines[end + 1]):
        end += 1
    return start, end


def align_table(lines: list[str], row: int) -> tuple[int, int, list[str]] | None:
    """The table around `row` with every column padded to one width and the
    separator row rebuilt (alignment colons kept). (start, end, new_lines)."""
    extent = table_extent(lines, row)
    if extent is None:
        return None
    start, end = extent
    rows = [split_cells(lines[r]) for r in range(start, end + 1)]
    seps = [is_separator_row(lines[r]) for r in range(start, end + 1)]
    ncols = max(len(cells) for cells in rows)
    # None = unmarked (renders left); an explicit :--- keeps its colon.
    aligns: list[str | None] = [None] * ncols
    if True in seps:
        for c, cell in enumerate(rows[seps.index(True)]):
            left, right = cell.startswith(":"), cell.endswith(":")
            if left or right:
                aligns[c] = "center" if left and right else "right" if right else "left"
    widths = [3] * ncols  # room for '---'
    for cells, sep in zip(rows, seps):
        if sep:
            continue
        for c, cell in enumerate(cells):
            widths[c] = max(widths[c], cell_len(cell))
    indent = lines[start][: len(lines[start]) - len(lines[start].lstrip())]
    out: list[str] = []
    for cells, sep in zip(rows, seps):
        cells = cells + [""] * (ncols - len(cells))
        fields = []
        for c in range(ncols):
            if sep:
                colon_l = ":" if aligns[c] in ("left", "center") else ""
                colon_r = ":" if aligns[c] in ("center", "right") else ""
                dashes = "-" * (widths[c] + 2 - len(colon_l) - len(colon_r))
                fields.append(colon_l + dashes + colon_r)
                continue
            pad = widths[c] - cell_len(cells[c])
            if aligns[c] == "right":
                field = " " * pad + cells[c]
            elif aligns[c] == "center":
                field = " " * (pad // 2) + cells[c] + " " * (pad - pad // 2)
            else:
                field = cells[c] + " " * pad
            fields.append(" " + field + " ")
        out.append(indent + "|" + "|".join(fields) + "|")
    return start, end, out


def _pipes(line: str) -> list[int]:
    return [m.start() for m in re.finditer(r"(?<!\\)\|", line)]


def cell_bounds(line: str) -> list[tuple[int, int]]:
    """(start, end) column of each cell's stripped content. An empty cell
    reports a zero-width span just inside its left pipe."""
    pipes = _pipes(line)
    bounds: list[tuple[int, int]] = []
    for a, b in zip(pipes, pipes[1:]):
        segment = line[a + 1 : b]
        stripped = segment.strip()
        if stripped:
            s = a + 1 + (len(segment) - len(segment.lstrip()))
            bounds.append((s, s + len(stripped)))
        else:
            spot = min(a + 2, b)
            bounds.append((spot, spot))
    return bounds


def cell_index(line: str, col: int) -> int:
    """Which cell of a table row the column falls in (clamped)."""
    pipes = _pipes(line)
    ncells = max(0, len(pipes) - 1)
    if ncells == 0:
        return 0
    before = sum(1 for p in pipes if p < col)
    return max(0, min(before - 1, ncells - 1))


def blank_row_like(line: str) -> str:
    """An empty table row with the same pipe geometry as `line`."""
    return re.sub(r"[^|]", " ", line)


# -- fill (M-q) --------------------------------------------------------------


def fill_paragraph(
    lines: list[str], row: int, width: int
) -> tuple[int, int, list[str]] | None:
    """Re-wrap the paragraph, list item, or quote block around `row` to
    `width` columns. Returns (start, end, new_lines), or None where filling
    would be wrong (blank line, heading, table, rule, fence, code block)."""
    if in_fence(lines, row):
        return None
    kind = _kind(lines[row])
    if kind in ("blank", "fence", "heading", "table", "rule"):
        return None
    if kind == "quote":
        return _fill_quote(lines, row, width)
    start = row
    if kind == "text":
        while start > 0 and _kind(lines[start - 1]) == "text":
            start -= 1
        if start > 0 and _kind(lines[start - 1]) == "item":
            start -= 1
            kind = "item"
    end = row
    while end + 1 < len(lines) and _kind(lines[end + 1]) == "text":
        end += 1
    if kind == "item":
        item = parse_item(lines[start])
        first = item.head
        # Hanging indent; inside a blockquote the quote prefix repeats.
        hang = item.quote + " " * (len(first) - len(item.quote))
        parts = [item.content.strip()]
    else:
        indent = lines[start][: len(lines[start]) - len(lines[start].lstrip())]
        if len(indent) >= 4:
            return None  # indented code block
        first = hang = indent
        parts = [lines[start].strip()]
    parts += [lines[r].strip() for r in range(start + 1, end + 1)]
    text = " ".join(part for part in parts if part)
    wrapped = textwrap.wrap(
        text,
        width=width,
        initial_indent=first,
        subsequent_indent=hang,
        break_long_words=False,
        break_on_hyphens=False,
    ) or [first.rstrip()]
    return start, end, wrapped


def _fill_quote(
    lines: list[str], row: int, width: int
) -> tuple[int, int, list[str]] | None:
    depth = QUOTE_RE.match(lines[row])["prefix"].count(">")

    def same_block(r: int) -> bool:
        if _kind(lines[r]) != "quote":
            return False
        match = QUOTE_RE.match(lines[r])
        return match["prefix"].count(">") == depth and bool(match["rest"].strip())

    start = end = row
    while start > 0 and same_block(start - 1):
        start -= 1
    while end + 1 < len(lines) and same_block(end + 1):
        end += 1
    text = " ".join(QUOTE_RE.match(lines[r])["rest"].strip() for r in range(start, end + 1))
    prefix = "> " * depth
    wrapped = textwrap.wrap(
        text,
        width=width,
        initial_indent=prefix,
        subsequent_indent=prefix,
        break_long_words=False,
        break_on_hyphens=False,
    ) or [prefix.rstrip()]
    return start, end, wrapped


def reformat(
    lines: list[str], row: int, width: int
) -> tuple[int, int, list[str], str] | None:
    """M-q: the context-appropriate reformat of the block at `row` —
    (start, end, new_lines, what), where `what` names what was done."""
    if in_fence(lines, row):
        return None
    if is_table_row(lines[row]):
        result = align_table(lines, row)
        return (*result, "table") if result is not None else None
    result = fill_paragraph(lines, row, width)
    return (*result, "paragraph") if result is not None else None


# -- ordered lists -----------------------------------------------------------


def renumber(lines: list[str], row: int) -> tuple[int, int, list[str]] | None:
    """Sequentially renumber every ordered run in the list block around
    `row` (contiguous item/continuation lines), at every nesting depth. A
    run keeps its first item's start number and each item's own delimiter;
    a bullet at the same depth breaks a run, and returning to a shallower
    depth ends the deeper runs. Returns (start, end, new_lines), or None
    when there is nothing to renumber / nothing changed."""

    def in_block(line: str) -> bool:
        return _kind(line) in ("item", "text")

    if not in_block(lines[row]):
        return None
    start = row
    while start > 0 and in_block(lines[start - 1]):
        start -= 1
    end = row
    while end + 1 < len(lines) and in_block(lines[end + 1]):
        end += 1
    while start <= end and _kind(lines[start]) != "item":
        start += 1  # leading text belongs to the paragraph above, not the list
    if start > end:
        return None
    new: list[str] = []
    counters: dict[tuple[str, int], int] = {}  # (quote, indent) -> next number
    for line in lines[start : end + 1]:
        item = parse_item(line)
        if item is None:
            new.append(line)
            continue
        depth = (item.quote, len(item.indent))
        for key in [k for k in counters if k[0] == item.quote and k[1] > depth[1]]:
            del counters[key]  # back at this depth: deeper runs ended
        m = ORDERED_RE.match(item.marker)
        if m is None:
            counters.pop(depth, None)  # a bullet breaks the ordered run
            new.append(line)
            continue
        number = counters.get(depth, int(m["number"]))
        counters[depth] = number + 1
        new.append(
            item.quote + item.indent + f"{number}{m['delim']}"
            + item.space + (item.box or "") + item.content
        )
    if new == lines[start : end + 1]:
        return None
    return start, end, new


def indent_item(lines: list[str], row: int) -> str | None:
    """The row's line nested one list level deeper (under its previous
    sibling's content column), or None when there is nothing to nest under
    or the line isn't a list item."""
    item = parse_item(lines[row])
    if item is None:
        return None
    for r in range(row - 1, -1, -1):
        k = _kind(lines[r])
        if k == "text":
            continue
        if k != "item":
            return None
        prev = parse_item(lines[r])
        if prev.quote != item.quote:
            return None
        if len(prev.indent) <= len(item.indent):
            target = len(prev.indent) + len(prev.marker) + len(prev.space) + len(prev.box or "")
            if target <= len(item.indent):
                return None  # already nested under it
            return item.quote + " " * target + item.marker + item.space + (item.box or "") + item.content
    return None


def outdent_item(lines: list[str], row: int) -> str | None:
    """The row's line moved one list level shallower (to its parent's
    indent, or column 0), or None when it isn't an indented list item."""
    item = parse_item(lines[row])
    if item is None or not item.indent:
        return None
    tail = item.marker + item.space + (item.box or "") + item.content
    for r in range(row - 1, -1, -1):
        k = _kind(lines[r])
        if k == "text":
            continue
        if k != "item":
            break
        prev = parse_item(lines[r])
        if prev.quote != item.quote:
            break
        if len(prev.indent) < len(item.indent):
            return item.quote + prev.indent + tail
    return item.quote + tail


# -- small helpers -------------------------------------------------------------


def toggle_checkbox(line: str) -> str | None:
    """Flip `[ ]` ↔ `[x]` on a task item; add a fresh `[ ]` to a plain list
    item. None when the line isn't a list item at all."""
    item = parse_item(line)
    if item is None:
        return None
    head = item.quote + item.indent + item.marker + item.space
    if item.box is not None:
        flipped = "[ ]" if "x" in item.box.lower() else "[x]"
        return head + flipped + item.box[3:] + item.content
    if (m := _BOX_RE.fullmatch(item.content.strip())) is not None:
        return head + ("[ ]" if m[1] in "xX" else "[x]")
    return head + "[ ] " + item.content


def toggle_emphasis(text: str, kind: str) -> str:
    """Wrap `text` in bold/italic/code markers, or unwrap when already
    wrapped. Bold and italic compose: `**x**` + italic → `***x***`."""
    if kind == "code":
        if len(text) >= 2 and text.startswith("`") and text.endswith("`"):
            return text[1:-1]
        return f"`{text}`"
    lead = len(text) - len(text.lstrip("*"))
    trail = len(text) - len(text.rstrip("*"))
    if lead + trail >= len(text):  # empty or nothing but stars
        wrap = "**" if kind == "bold" else "*"
        return wrap + text + wrap
    stars = min(lead, trail)
    if kind == "bold":
        return text[2:-2] if stars >= 2 else "**" + text + "**"
    return text[1:-1] if stars % 2 == 1 else "*" + text + "*"


def word_at(line: str, col: int) -> tuple[int, int] | None:
    """The word (\\w+ run) the column touches, as (start, end)."""
    for m in _WORD_RE.finditer(line):
        if m.start() <= col <= m.end():
            return m.start(), m.end()
    return None


def is_url(text: str) -> bool:
    return _URL_RE.fullmatch(text.strip()) is not None
