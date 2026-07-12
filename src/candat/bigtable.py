"""A windowed table view for arbitrarily large delimited files.

The classic CSV viewer (``csvview``) feeds rows into Textual's DataTable,
which holds everything it is given — fine up to a couple hundred thousand
rows, a dead end for the multi-million-row files this widget exists for.

Here the file stays on disk behind the pager's sparse line index (built by a
background worker; the table renders and scrolls immediately while indexing
continues, csvlens-style). Only the visible viewport is read and parsed per
frame, so ten million rows cost the same as a hundred. Search scans the whole
file once on a worker thread and collects *every* matching row number, so
``n`` / ``N`` are instant bisects and the status bar shows "match k/M" — the
same design csvlens uses.

Limitation: rows are located by newlines, so a quoted field containing a
newline (rare outside strict RFC-4180 CSV) would mis-split; callers detect
that in a sample and keep such files on the classic capped table.
"""

from __future__ import annotations

import bisect
import csv
from functools import partial
from pathlib import Path

from rich.cells import cell_len
from rich.style import Style
from rich.text import Text
from textual import events
from textual.binding import Binding
from textual.message import Message
from textual.widget import Widget
from textual.worker import get_current_worker

from .csvview import CELL_MAX, delimiter_label, parse_delimiter, sniff_sample
from .pager import CHUNK, SPARSE_STEP, LineIndex, _cell_crop, _FileReader, smartcase_pattern

MAX_COL_FRACTION = 0.35  # one column may take at most this much of the width
COL_GAP = 2
FETCH_CAP_BYTES = 8 * CHUNK  # never read more than this for one viewport
PROGRESS_EVERY = 32 * CHUNK


def has_quoted_newline(sample: str) -> bool:
    """True if the sample has a newline inside a quoted field — the one case
    a line-based row index would mis-split."""
    in_quote = False
    for ch in sample:
        if ch == '"':
            in_quote = not in_quote
        elif ch == "\n" and in_quote:
            return True
    return False


def _line_offset(reader: _FileReader, index: LineIndex, line: int) -> int:
    """Byte offset where `line` begins, via the sparse index (pager logic)."""
    block = min(line // SPARSE_STEP, len(index.offsets) - 1)
    current = block * SPARSE_STEP
    pos = index.offsets[block]
    while current < line:
        chunk = reader.read(pos, CHUNK)
        if not chunk:
            return reader.size
        i = 0
        while (nl := chunk.find(b"\n", i)) != -1:
            i = nl + 1
            current += 1
            if current == line:
                return pos + i
        pos += len(chunk)
    return pos


class BigTable(Widget, can_focus=True):
    """Read-only, windowed table over a huge delimited file."""

    DEFAULT_CSS = """
    BigTable { background: $background; color: $foreground; }
    """

    BINDINGS = [
        Binding("down,ctrl+n", "cursor(1)", "down", show=False),
        Binding("up,ctrl+p", "cursor(-1)", "up", show=False),
        Binding("pagedown,ctrl+v,space", "page(1)", "page down", show=False),
        Binding("pageup,alt+v", "page(-1)", "page up", show=False),
        Binding("right,ctrl+f", "scroll_cols(1)", "columns right", show=False),
        Binding("left,ctrl+b", "scroll_cols(-1)", "columns left", show=False),
        Binding("ctrl+home", "goto_top", "top", show=False),
        Binding("ctrl+end", "goto_bottom", "bottom", show=False),
    ]

    HEADER_STYLE = Style(bold=True, bgcolor="#eef1f4", color="#24292f")
    CURSOR_STYLE = Style(bgcolor="#dbe6f6")
    ZEBRA_STYLE = Style(bgcolor="#fafbfc")
    GUTTER_STYLE = Style(color="#8b949e")
    STATUS_STYLE = Style(bgcolor="#f4f4f4", color="#24292f")
    DIM_STYLE = Style(color="#8b949e", bgcolor="#f4f4f4")
    HIGHLIGHT = Style(bgcolor="yellow", color="black")

    class Moved(Message):
        """Posted when position/state changes (for the app status bar)."""

    def __init__(self, path: Path | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.path = path
        self._reader: _FileReader | None = None
        self._index = LineIndex()
        self._indexing = True
        self._generation = 0
        self._delimiter = ","
        self._has_header = True
        self._columns: list[str] = []
        self._widths: list[int] = []
        self.cursor_row = 0  # data-row index (0-based, header excluded)
        self.top_row = 0
        self.col_offset = 0
        # Search: every matching data-row number, collected by one scan.
        self._last_query = ""
        self._matches: list[int] = []
        self._search_seq = 0
        self._search_running = False

    # -- lifecycle -----------------------------------------------------------

    def load(self, path: Path, delimiter: str | None = None) -> None:
        self._generation += 1
        self._search_seq += 1
        self._search_running = False
        self.path = path
        self._close_reader()
        self._index = LineIndex()
        self._indexing = True
        self._columns = []
        self._widths = []
        self.cursor_row = self.top_row = self.col_offset = 0
        self._last_query = ""
        self._matches = []
        try:
            reader = _FileReader(path)
        except OSError:
            self.refresh()
            return
        self._reader = reader
        sample = reader.read(0, 8192).decode("utf-8", "replace")
        sniffed, self._has_header = sniff_sample(sample, path.suffix.lower())
        self._delimiter = delimiter or sniffed
        self.refresh()
        # Index in the background; the table renders and scrolls immediately,
        # growing as the worker gets further into the file (csvlens-style).
        self.run_worker(
            partial(self._index_worker, self._generation),
            thread=True,
            exclusive=True,
            group="bigtable-index",
        )

    def _index_worker(self, gen: int) -> None:
        worker = get_current_worker()
        reader = self._reader
        if reader is None:
            return
        last = 0

        def on_chunk(scanned: int, _size: int) -> None:
            nonlocal last
            if scanned - last >= PROGRESS_EVERY:
                last = scanned
                self.app.call_from_thread(self._progress, gen)

        # The UI thread reads line_count/offsets while this appends — safe
        # enough under the GIL, and the pager's follow mode does the same.
        self._index.scan(
            reader,
            should_stop=lambda: worker.is_cancelled or gen != self._generation,
            on_chunk=on_chunk,
        )
        self.app.call_from_thread(self._progress, gen, done=True)

    def _progress(self, gen: int, done: bool = False) -> None:
        if gen != self._generation:
            return
        if done:
            self._indexing = False
        self._moved()

    def _close_reader(self) -> None:
        if self._reader is not None:
            self._reader.close()
            self._reader = None

    def unload(self) -> None:
        self._generation += 1
        self._search_seq += 1
        self._search_running = False
        self._close_reader()
        self.path = None
        self._index = LineIndex()
        self._indexing = True
        self._matches = []
        self._last_query = ""

    def on_unmount(self) -> None:
        self._generation += 1
        self._search_seq += 1
        self._close_reader()

    def _moved(self) -> None:
        self.refresh()
        self.post_message(self.Moved())

    # -- data access -----------------------------------------------------------

    @property
    def _header_offset(self) -> int:
        return 1 if self._has_header else 0

    @property
    def total_rows(self) -> int:
        """Data rows indexed so far (grows while indexing)."""
        return max(0, self._index.line_count - self._header_offset)

    def _read_lines(self, start_line: int, count: int) -> list[str]:
        """Raw file lines [start_line, start_line+count), from the index."""
        reader, index = self._reader, self._index
        if reader is None or count <= 0:
            return []
        available = index.line_count
        if start_line >= available:
            return []
        count = min(count, available - start_line)
        offset = _line_offset(reader, index, start_line)
        data = b""
        pos = offset
        while data.count(b"\n") < count and pos < reader.size:
            if len(data) >= FETCH_CAP_BYTES:
                break  # pathological line lengths: show what we have
            chunk = reader.read(pos, CHUNK)
            if not chunk:
                break
            data += chunk
            pos += len(chunk)
        return data.decode("utf-8", "replace").split("\n")[:count]

    def fetch_rows(self, start_row: int, count: int) -> list[list[str]]:
        """Parsed data rows [start_row, start_row+count)."""
        lines = self._read_lines(start_row + self._header_offset, count)
        # Each line is one record (no embedded newlines by construction), so
        # csv handles in-line quoting correctly.
        return list(csv.reader(lines, delimiter=self._delimiter))

    def _ensure_columns(self) -> None:
        if self._columns or self._index.line_count == 0:
            return
        first = self._read_lines(0, 1)
        if not first:
            return
        cells = next(csv.reader(first, delimiter=self._delimiter), [])
        if self._has_header:
            self._columns = [c or f"col{i + 1}" for i, c in enumerate(cells)]
        else:
            self._columns = [f"col{i + 1}" for i in range(len(cells))]

    # -- rendering -------------------------------------------------------------

    def _update_widths(self, rows: list[list[str]]) -> None:
        """Grow column widths from the header + the rows in view. Monotonic,
        so columns don't jiggle while scrolling; capped per column."""
        cap = max(8, int(self.size.width * MAX_COL_FRACTION))
        for cells in [self._columns, *rows]:
            for i, cell in enumerate(cells):
                if len(cell) > CELL_MAX:
                    cell = cell[:CELL_MAX]
                w = min(cell_len(cell), cap)
                if i >= len(self._widths):
                    self._widths.append(w)
                elif w > self._widths[i]:
                    self._widths[i] = w

    def _highlight_cells(self, text: Text, row_repr: str, base: int) -> None:
        """Style every occurrence of the query in an already-rendered row."""
        query = self._last_query
        if not query:
            return
        fold = query == query.lower()
        hay = row_repr.lower() if fold else row_repr
        needle = query.lower() if fold else query
        start = 0
        while True:
            i = hay.find(needle, start)
            if i == -1:
                break
            text.stylize(self.HIGHLIGHT, base + i, base + i + len(needle))
            start = i + len(needle)

    def render(self) -> Text:
        width, height = self.size.width, self.size.height
        self._ensure_columns()
        if height < 2 or self._reader is None:
            return Text(end="")
        body_rows = max(1, height - 2)  # header + status
        # Keep the cursor inside the window.
        self.cursor_row = max(0, min(self.cursor_row, max(0, self.total_rows - 1)))
        if self.cursor_row < self.top_row:
            self.top_row = self.cursor_row
        if self.cursor_row >= self.top_row + body_rows:
            self.top_row = self.cursor_row - body_rows + 1
        rows = self.fetch_rows(self.top_row, body_rows)
        self._update_widths(rows)
        gutter = max(4, len(f"{self._index.line_count:,}")) + 1
        cols = range(self.col_offset, len(self._widths))
        out = Text(no_wrap=True, end="")

        def line_for(cells: list[str], label: str, style: Style, hl: bool) -> Text:
            line = Text(end="")
            gut_style = self.HEADER_STYLE if style is self.HEADER_STYLE else self.GUTTER_STYLE
            line.append(f"{label:>{gutter}} ", gut_style)
            body = []
            for i in cols:
                cell = cells[i] if i < len(cells) else ""
                if len(cell) > CELL_MAX:
                    cell = cell[:CELL_MAX] + "…"
                cropped, _cut = _cell_crop(cell, 0, self._widths[i])
                body.append(cropped + " " * (self._widths[i] - cell_len(cropped)))
            row_repr = (" " * COL_GAP).join(body)
            base = len(line)
            line.append(row_repr, style)
            if hl:
                self._highlight_cells(line, row_repr, base)
            plain = line.plain
            if cell_len(plain) > width:
                keep, _ = _cell_crop(plain, 0, width - 1)
                clipped = Text(keep, end="")
                for span in line.spans:
                    if span.start < len(keep):
                        clipped.stylize(span.style, span.start, min(span.end, len(keep)))
                clipped.append("›", self.GUTTER_STYLE)
                return clipped
            if style is not None and style != Style():
                line.append(" " * (width - cell_len(plain)), style)
            return line

        out.append_text(line_for(self._columns, "#", self.HEADER_STYLE, hl=False))
        for j in range(body_rows):
            out.append("\n")
            row_index = self.top_row + j
            if row_index >= self.total_rows or j >= len(rows):
                continue
            if row_index == self.cursor_row:
                style = self.CURSOR_STYLE
            elif row_index % 2:
                style = self.ZEBRA_STYLE
            else:
                style = Style()
            label = f"{row_index + 1 + self._header_offset:,}"
            out.append_text(line_for(rows[j], label, style, hl=bool(self._last_query)))
        out.append("\n")
        status = self.status_line(width)
        out.append_text(status)
        return out

    def status_line(self, width: int) -> Text:
        name = self.path.name if self.path else ""
        total = self.total_rows
        more = "" if not self._indexing else "+"
        row = self.cursor_row + 1 if total else 0
        parts = [
            f" {name}",
            f"row {row:,}/{total:,}{more}",
            f"{len(self._columns)} cols",
            f"sep {delimiter_label(self._delimiter)}",
        ]
        if self._search_running:
            parts.append("searching…")
        elif self._matches:
            k = bisect.bisect_right(self._matches, self.cursor_row)
            parts.append(f"match {k:,}/{len(self._matches):,}")
        text = Text("   ".join(parts), self.STATUS_STYLE, end="")
        hint = "   / search  C-s/n C-r/N next/prev  d delim  g/G ends"
        text.append(hint, self.DIM_STYLE)
        pad = width - cell_len(text.plain)
        if pad > 0:
            text.append(" " * pad, self.STATUS_STYLE)
        return text

    # -- navigation --------------------------------------------------------------

    def action_cursor(self, amount: int) -> None:
        self.cursor_row = max(0, min(self.cursor_row + amount, max(0, self.total_rows - 1)))
        self._moved()

    def action_page(self, direction: int) -> None:
        self.action_cursor(direction * max(1, self.size.height - 3))

    def action_scroll_cols(self, amount: int) -> None:
        limit = max(0, len(self._widths) - 1)
        self.col_offset = max(0, min(self.col_offset + amount, limit))
        self._moved()

    def action_goto_top(self) -> None:
        self.cursor_row = 0
        self._moved()

    def action_goto_bottom(self) -> None:
        self.cursor_row = max(0, self.total_rows - 1)
        if self._indexing:
            self.app.notify("Still indexing — jumped to the rows seen so far", timeout=2)
        self._moved()

    def goto_row(self, row: int) -> None:
        self.cursor_row = max(0, min(row, max(0, self.total_rows - 1)))
        self._moved()

    # -- search --------------------------------------------------------------------

    @property
    def searching(self) -> bool:
        return bool(self._last_query) or self._search_running

    def search(self, term: str) -> None:
        """Scan the whole file once on a worker, collecting every matching
        row; n/N then bisect the result instantly (csvlens-style)."""
        if self._reader is None:
            return
        self._last_query = term
        self._matches = []
        self._search_seq += 1
        self._search_running = True
        self._moved()
        pattern = smartcase_pattern(term)
        self.run_worker(
            partial(
                self._search_worker,
                self._reader.dup(),
                pattern,
                term,
                self._generation,
                self._search_seq,
                self._header_offset,
            ),
            thread=True,
            exclusive=True,
            group="bigtable-search",
        )

    def _search_worker(self, reader, pattern, term, gen, seq, header_offset) -> None:
        worker = get_current_worker()
        overlap = 4 * len(term) + 8
        matches: list[int] = []
        pos = 0
        base_line = 0
        size = reader.size
        try:
            while pos < size:
                if worker.is_cancelled or seq != self._search_seq or gen != self._generation:
                    return
                limit = min(CHUNK, size - pos)
                window = reader.read(pos, limit + overlap)
                if not window:
                    break
                nl_upto = 0
                prev = 0
                for match in pattern.finditer(window):
                    if match.start() >= limit:
                        break
                    nl_upto += window.count(b"\n", prev, match.start())
                    prev = match.start()
                    row = base_line + nl_upto - header_offset
                    if row >= 0 and (not matches or matches[-1] != row):
                        matches.append(row)
                base_line += window.count(b"\n", 0, limit)
                pos += limit
        finally:
            reader.close()
        self.app.call_from_thread(self._search_done, gen, seq, term, matches)

    def _search_done(self, gen: int, seq: int, term: str, matches: list[int]) -> None:
        if gen != self._generation or seq != self._search_seq:
            return
        self._search_running = False
        if not matches:
            self._last_query = ""
            self.app.notify(f"Not found: {term}", severity="warning", timeout=2)
            self._moved()
            return
        self._matches = matches
        # Jump to the first match at or after the cursor (wrapping).
        i = bisect.bisect_left(matches, self.cursor_row)
        self.goto_row(matches[i] if i < len(matches) else matches[0])

    def step_match(self, forward: bool) -> None:
        if self._search_running:
            return
        if not self._matches:
            return
        if forward:
            i = bisect.bisect_right(self._matches, self.cursor_row)
            if i >= len(self._matches):
                i = 0
        else:
            i = bisect.bisect_left(self._matches, self.cursor_row) - 1
            if i < 0:
                i = len(self._matches) - 1
        self.goto_row(self._matches[i])

    def cancel_search(self) -> None:
        self._search_seq += 1  # a running worker sees a stale seq and stops
        self._search_running = False
        self._last_query = ""
        self._matches = []
        self._moved()

    def set_delimiter(self, delimiter: str) -> None:
        """Re-parse with a new delimiter. The row index is line-based, so it
        stays valid — only parsing and widths change."""
        if delimiter == self._delimiter:
            return
        self._delimiter = delimiter
        self._columns = []
        self._widths = []
        self.col_offset = 0
        self._moved()

    # -- keys ------------------------------------------------------------------

    def _prompt_search(self) -> None:
        from .dialogs import PromptScreen

        def run(term: str | None) -> None:
            if term:
                self.search(term)

        self.app.push_screen(PromptScreen("Search table:"), run)

    def on_key(self, event: events.Key) -> None:
        from .dialogs import PromptScreen

        key = event.key
        if key in ("slash", "/"):
            event.stop()
            event.prevent_default()
            self._prompt_search()
        elif key == "ctrl+s":
            event.stop()
            event.prevent_default()
            if self._last_query:
                self.step_match(True)
            else:
                self._prompt_search()
        elif key == "ctrl+r":
            event.stop()
            event.prevent_default()
            if self._last_query:
                self.step_match(False)
            else:
                self._prompt_search()
        elif key == "n":
            event.stop()
            self.step_match(True)
        elif key == "N":
            event.stop()
            self.step_match(False)
        elif key == "g":
            event.stop()
            self.action_goto_top()
        elif key == "G":
            event.stop()
            self.action_goto_bottom()
        elif key == "d":
            event.stop()

            def apply(value: str | None) -> None:
                if not value:
                    return
                delimiter = parse_delimiter(value)
                if delimiter is None:
                    self.app.notify(
                        f"Not a delimiter: {value!r} (use , ; | tab space or one character)",
                        severity="error",
                        timeout=3,
                    )
                    return
                self.set_delimiter(delimiter)

            self.app.push_screen(
                PromptScreen(
                    f"Delimiter [{delimiter_label(self._delimiter)}] "
                    "(, ; | tab space or any character):"
                ),
                apply,
            )
        elif key == "ampersand":
            event.stop()
            self.app.notify(
                "Row filter isn't available on huge tables yet — use / search",
                timeout=3,
            )
        elif key == "escape" and self.searching:
            event.stop()
            self.cancel_search()

    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        event.stop()
        self.action_cursor(3)

    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        event.stop()
        self.action_cursor(-3)
