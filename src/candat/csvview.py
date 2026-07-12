"""CSV/TSV table viewer, inspired by csvlens (github.com/YS-L/csvlens).

Streams the file instead of loading it whole: an initial batch of rows is
shown and more are read as the cursor approaches the end, so opening a huge
file is instant and memory grows only with how far you scroll. Search (C-s
or /) streams forward through the file; & applies a regex row filter by
re-streaming from the top. Rows keep their original file line numbers as
labels.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Iterator, TextIO

from rich.style import Style
from rich.text import Text
from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.coordinate import Coordinate
from textual.widgets import DataTable, Static

HIGHLIGHT = Style(bgcolor="yellow", color="black")

CSV_SUFFIXES = {".csv", ".tsv"}

INITIAL_ROWS = 500
BATCH_ROWS = 2000
MAX_ROWS = 200_000  # hard cap on rows held in the table
CELL_MAX = 120  # display truncation for very wide cells
LOOKAHEAD = 100  # load more when the cursor gets this close to the end


def sniff_dialect(path: Path) -> tuple[str, bool]:
    """Returns (delimiter, has_header)."""
    try:
        sample = path.open(errors="replace").read(8192)
    except OSError:
        return ",", True
    if path.suffix.lower() == ".tsv":
        delimiter = "\t"
    else:
        try:
            delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
        except csv.Error:
            delimiter = ","
    try:
        has_header = csv.Sniffer().has_header(sample)
    except csv.Error:
        has_header = True
    return delimiter, has_header


class CsvTable(DataTable):
    BINDINGS = [
        Binding("ctrl+n", "cursor_down", "down", show=False),
        Binding("ctrl+p", "cursor_up", "up", show=False),
        Binding("ctrl+v", "page_down", "page down", show=False),
        Binding("alt+v", "page_up", "page up", show=False),
        Binding("ctrl+f", "cursor_right", "right", show=False),
        Binding("ctrl+b", "cursor_left", "left", show=False),
        Binding("ctrl+a", "scroll_home", "line start", show=False),
        Binding("ctrl+e", "scroll_end", "line end", show=False),
    ]


class CsvViewer(Vertical):
    """A csvlens-style table view of a delimited file."""

    DEFAULT_CSS = """
    CsvViewer {
        width: 1fr;
    }
    CsvViewer CsvTable {
        height: 1fr;
    }
    CsvViewer #csv-status {
        height: 1;
        padding: 0 1;
        background: $panel;
        color: $foreground;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._path: Path | None = None
        self._delimiter = ","
        self._has_header = True
        self._columns: list[str] = []
        self._file: TextIO | None = None
        self._reader: Iterator[list[str]] | None = None
        self._line_no = 0  # original file line of the last row read
        self._loaded = 0
        self._exhausted = False
        self._filter: re.Pattern | None = None
        self._last_search: re.Pattern | None = None
        self.mtime: float | None = None

    @property
    def table(self) -> CsvTable:
        return self.query_one(CsvTable)

    def compose(self) -> ComposeResult:
        table = CsvTable()
        table.cursor_type = "row"
        table.zebra_stripes = True
        yield table
        yield Static(id="csv-status")

    # -- streaming ---------------------------------------------------------

    def open_file(self, path: Path) -> None:
        self._path = path
        self._delimiter, self._has_header = sniff_dialect(path)
        try:
            self.mtime = path.stat().st_mtime
        except OSError:
            self.mtime = None
        self._restart(keep_filter=False)

    def reload(self) -> None:
        """Re-read the file (external change), keeping filter and cursor."""
        if self._path is None:
            return
        try:
            self.mtime = self._path.stat().st_mtime
        except OSError:
            self.mtime = None
        cursor = self.table.cursor_row
        target = max(self._loaded, INITIAL_ROWS)
        self._restart(keep_filter=True, initial=min(target, MAX_ROWS))
        if self.table.row_count:
            self.table.move_cursor(row=min(cursor, self.table.row_count - 1))

    def _restart(self, keep_filter: bool, initial: int = INITIAL_ROWS) -> None:
        if not keep_filter:
            self._filter = None
        if self._file is not None:
            self._file.close()
            self._file = None
        table = self.table
        table.clear(columns=True)
        self._loaded = 0
        self._line_no = 0
        self._exhausted = False
        if self._path is None:
            return
        try:
            self._file = self._path.open(newline="", errors="replace")
        except OSError as error:
            self.query_one("#csv-status", Static).update(f" cannot read: {error}")
            return
        self._reader = csv.reader(self._file, delimiter=self._delimiter)
        first = next(self._reader, None)
        if first is None:
            self._columns = []
            self._update_status()
            return
        if self._has_header:
            self._columns = [c or f"col{i + 1}" for i, c in enumerate(first)]
            self._line_no = 1
        else:
            self._columns = [f"col{i + 1}" for i in range(len(first))]
            underlying = self._reader

            def chain() -> Iterator[list[str]]:
                yield first
                yield from underlying

            self._reader = chain()
        table.add_columns(*self._columns)
        self.load_more(initial)

    def _match_filter(self, row: list[str]) -> bool:
        if self._filter is None:
            return True
        return any(self._filter.search(cell) for cell in row)

    def load_more(self, count: int = BATCH_ROWS) -> int:
        """Read up to `count` more (filtered) rows into the table; returns
        how many were added."""
        if self._reader is None or self._exhausted:
            return 0
        table = self.table
        width = len(self._columns)
        added = 0
        while added < count:
            if self._loaded >= MAX_ROWS:
                self._exhausted = True
                self.app.notify(
                    f"Showing the first {MAX_ROWS:,} rows", severity="warning"
                )
                break
            row = next(self._reader, None)
            if row is None:
                self._exhausted = True
                if self._file is not None:
                    self._file.close()
                    self._file = None
                break
            self._line_no += 1
            if not self._match_filter(row):
                continue
            cells = [self._make_cell(cell) for cell in row[:width]]
            cells += [self._make_cell("")] * (width - len(cells))
            table.add_row(*cells, label=str(self._line_no))
            self._loaded += 1
            added += 1
        self._update_status()
        return added

    def load_all(self) -> None:
        while not self._exhausted:
            self.load_more(50_000)

    # -- cell highlighting ---------------------------------------------------

    def _make_cell(self, value: str) -> Text:
        """A cell as a Rich Text (literal, no markup), with every occurrence of
        the active search term highlighted."""
        if len(value) > CELL_MAX:
            value = value[:CELL_MAX] + "…"
        text = Text(value, no_wrap=True)
        regex = self._last_search
        if regex is not None:
            for m in regex.finditer(value):
                if m.end() > m.start():
                    text.stylize(HIGHLIGHT, m.start(), m.end())
        return text

    def _restyle_loaded(self) -> None:
        """Re-highlight the already-loaded rows in place (keeps scroll/cursor);
        rows still to stream in are highlighted as they load."""
        table = self.table
        cols = len(self._columns)
        for r in range(table.row_count):
            for c in range(cols):
                cell = table.get_cell_at(Coordinate(r, c))
                value = cell.plain if isinstance(cell, Text) else str(cell)
                table.update_cell_at(
                    Coordinate(r, c), self._make_cell(value), update_width=False
                )

    @property
    def searching(self) -> bool:
        return self._last_search is not None

    def cancel_search(self) -> None:
        """Drop the search term and clear the cell highlighting (C-g / Esc)."""
        if self._last_search is None:
            return
        self._last_search = None
        self._restyle_loaded()
        self._update_status()

    # -- search and filter ---------------------------------------------------

    @staticmethod
    def _compile(pattern: str) -> re.Pattern | None:
        flags = re.IGNORECASE if pattern == pattern.lower() else 0
        try:
            return re.compile(pattern, flags)
        except re.error:
            return None

    def search(self, pattern: str) -> None:
        regex = self._compile(pattern)
        if regex is None:
            self.app.notify(f"Bad regex: {pattern}", severity="error", timeout=2)
            return
        self._last_search = regex
        self._restyle_loaded()  # highlight matches in the rows already on screen
        self.search_next()

    def search_next(self) -> None:
        regex = self._last_search
        if regex is None:
            return
        table = self.table
        row = table.cursor_row + 1
        while True:
            while row < table.row_count:
                if any(regex.search(str(cell)) for cell in table.get_row_at(row)):
                    table.move_cursor(row=row)
                    self._update_status()
                    return
                row += 1
            if self._exhausted or not self.load_more(BATCH_ROWS):
                self.app.notify("No more matches", timeout=2)
                return

    def apply_filter(self, pattern: str) -> None:
        if not pattern:
            self._filter = None
            self._restart(keep_filter=False)
            return
        regex = self._compile(pattern)
        if regex is None:
            self.app.notify(f"Bad regex: {pattern}", severity="error", timeout=2)
            return
        self._filter = regex
        self._restart(keep_filter=True)

    # -- interaction ---------------------------------------------------------

    def _update_status(self) -> None:
        table = self.table
        name = self._path.name if self._path else ""
        row = table.cursor_row + 1 if table.row_count else 0
        more = "" if self._exhausted else "+"
        parts = [
            f" {name}",
            f"row {row}/{table.row_count}{more}",
            f"{len(self._columns)} cols",
        ]
        if self._filter is not None:
            parts.append(f"filter: {self._filter.pattern}")
        parts.append("[dim]/ search  C-s/n next  & filter  G end[/]")
        self.query_one("#csv-status", Static).update("   ".join(parts))

    @on(DataTable.RowHighlighted)
    def _row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if (
            not self._exhausted
            and self.table.cursor_row >= self.table.row_count - LOOKAHEAD
        ):
            self.load_more(BATCH_ROWS)
        self._update_status()

    def _prompt_search(self) -> None:
        from .dialogs import PromptScreen

        def do_search(pattern: str | None) -> None:
            if pattern:
                self.search(pattern)

        self.app.push_screen(PromptScreen("Search table (regex):"), do_search)

    def on_key(self, event: events.Key) -> None:
        from .dialogs import PromptScreen

        key = event.key
        if key in ("slash", "/"):
            event.stop()
            event.prevent_default()
            self._prompt_search()
        elif key == "ctrl+s":
            # C-s repeats to the next match when a search is active (like the
            # editor / pager); otherwise it starts one.
            event.stop()
            event.prevent_default()
            if self._last_search is not None:
                self.search_next()
            else:
                self._prompt_search()
        elif key == "n":
            event.stop()
            self.search_next()
        elif key == "escape" and self.searching:
            # Esc (like C-g) clears the search highlight.
            event.stop()
            self.cancel_search()
        elif key == "ampersand":
            event.stop()
            event.prevent_default()

            def do_filter(pattern: str | None) -> None:
                if pattern is not None:
                    self.apply_filter(pattern)

            self.app.push_screen(
                PromptScreen("Filter rows (regex, empty clears):"), do_filter
            )
        elif key == "g":
            event.stop()
            self.table.move_cursor(row=0)
        elif key == "G":
            event.stop()
            self.load_all()
            if self.table.row_count:
                self.table.move_cursor(row=self.table.row_count - 1)

    def on_unmount(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None
