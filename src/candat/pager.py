"""A windowed, `less`-style pager for very large files.

The file stays on disk and is read with :func:`os.pread` — never mmapped, so a
file truncated or rotated underneath us (logrotate on the log being viewed)
yields short reads instead of killing the whole process with SIGBUS. A
*sparse* line-offset index (one byte offset every ``SPARSE_STEP`` lines) gives
random access to any line without holding the whole file in memory. Only the
visible viewport is read and rendered, and a single file line is read at most
``MAX_LINE_BYTES`` deep for display, so even a gigabyte-long single line
(minified JSON) stays cheap. Wrapping and cropping count terminal cells, not
characters, so tabs, CJK and emoji line up. Searching scans the file in
chunks on a worker thread and is cancellable, so the UI never blocks on a
big scan.
"""

from __future__ import annotations

import bisect
import os
import re
from collections import OrderedDict
from functools import partial
from pathlib import Path

from rich.cells import cell_len, chop_cells
from rich.style import Style
from rich.text import Text
from textual import events
from textual.binding import Binding
from textual.message import Message
from textual.widget import Widget
from textual.worker import get_current_worker

SPARSE_STEP = 512  # index a byte offset every 512 lines
CHUNK = 1 << 20  # scan/read block size (1 MiB)
MAX_LINE_BYTES = 64 * 1024  # one line is read at most this deep for display
TABSTOP = 8
FOLLOW_INTERVAL = 1.0  # seconds between follow-mode polls
FOLLOW_SCAN_BUDGET = 64 * CHUNK  # max new bytes indexed per follow poll


class _FileReader:
    """pread-based random access to a file.

    Reads never fault: if the file shrinks (logrotate, ``> file``) a read
    simply comes back short or empty, unlike mmap where touching a vanished
    page kills the process with SIGBUS.
    """

    def __init__(self, path: Path) -> None:
        self.fd = os.open(str(path), os.O_RDONLY)
        self.size = os.fstat(self.fd).st_size

    def refresh_size(self) -> int:
        try:
            self.size = os.fstat(self.fd).st_size
        except OSError:
            self.size = 0
        return self.size

    def read(self, start: int, length: int) -> bytes:
        if start < 0 or length <= 0:
            return b""
        try:
            return os.pread(self.fd, length, start)
        except (OSError, ValueError):
            return b""  # closed underneath a worker, or the file is gone

    def dup(self) -> "_FileReader":
        """An independent handle to the same file, for a worker thread (the
        widget may close or replace its own reader while the worker runs)."""
        clone = object.__new__(_FileReader)
        clone.fd = os.dup(self.fd)
        clone.size = self.size
        return clone

    def close(self) -> None:
        try:
            os.close(self.fd)
        except OSError:
            pass
        self.fd = -1


class LineIndex:
    """Sparse map from line number to byte offset, plus the total line count.

    Built by scanning for newlines in chunks; :meth:`scan` resumes from where
    it stopped, so a growing file (follow mode) only has its new tail scanned.
    """

    def __init__(self) -> None:
        self.offsets: list[int] = [0]  # offsets[k] = start of line k*SPARSE_STEP
        self.newlines = 0
        self.scanned = 0  # bytes of the file scanned so far
        self.last_line_start = 0
        self.complete = False

    @property
    def line_count(self) -> int:
        tail = 1 if self.scanned > self.last_line_start else 0
        return self.newlines + tail

    def scan(self, reader: _FileReader, should_stop=None) -> bool:
        """Scan ``[self.scanned, reader.size)``; False if stopped early."""
        size = reader.size
        while self.scanned < size:
            if should_stop is not None and should_stop():
                return False
            chunk = reader.read(self.scanned, min(CHUNK, size - self.scanned))
            if not chunk:  # the file shrank underneath us
                break
            base = self.scanned
            pos = 0
            while (nl := chunk.find(b"\n", pos)) != -1:
                pos = nl + 1
                self.newlines += 1
                self.last_line_start = base + pos
                if self.newlines % SPARSE_STEP == 0:
                    self.offsets.append(base + pos)
            self.scanned += len(chunk)
        self.complete = True
        return True


def smartcase_pattern(query: str) -> re.Pattern[bytes]:
    """A bytes pattern for a literal query: an all-lowercase query matches
    case-insensitively, anything with an uppercase character matches exactly.
    Case folding is full Unicode — done by alternating each character's case
    forms, since bytes-level ``re.IGNORECASE`` only folds ASCII."""
    if query != query.lower():
        return re.compile(re.escape(query.encode("utf-8", "replace")))
    parts: list[bytes] = []
    for ch in query:
        forms = sorted(
            {re.escape(f.encode("utf-8", "replace")) for f in (ch, ch.upper(), ch.title())}
        )
        if len(forms) == 1:
            parts.append(forms[0])
        else:
            parts.append(b"(?:" + b"|".join(forms) + b")")
    return re.compile(b"".join(parts))


def _match_overlap(query: str) -> int:
    """Upper bound on the encoded length of a match (chunk-scan overlap)."""
    return sum(
        max(len(form.encode("utf-8", "replace")) for form in (ch, ch.upper()))
        for ch in query
    ) + 8


def _scan_forward(reader, pattern, start, end, overlap, should_stop):
    """Offset of the first match starting in [start, end); -1 if none,
    None if cancelled. Reads `overlap` extra bytes per chunk so a match
    straddling a chunk boundary is still seen whole."""
    pos = start
    while pos < end:
        if should_stop():
            return None
        limit = min(CHUNK, end - pos)
        window = reader.read(pos, limit + overlap)
        if not window:
            return -1
        match = pattern.search(window)
        if match is not None and match.start() < limit:
            return pos + match.start()
        pos += limit
    return -1


def _scan_backward(reader, pattern, before, overlap, should_stop):
    """Offset of the last match starting in [0, before); -1 if none,
    None if cancelled."""
    hi = before
    while hi > 0:
        if should_stop():
            return None
        lo = max(0, hi - CHUNK)
        window = reader.read(lo, hi - lo + overlap)
        if not window:
            return -1
        found = -1
        for match in pattern.finditer(window):
            if lo + match.start() >= hi:
                break
            found = lo + match.start()
        if found >= 0:
            return found
        hi = lo
    return -1


def _count_line(reader: _FileReader, index: LineIndex, offset: int) -> int:
    """The line number containing a byte offset, via the sparse index."""
    block = max(0, bisect.bisect_right(index.offsets, offset) - 1)
    line = block * SPARSE_STEP
    pos = index.offsets[block]
    while pos < offset:
        chunk = reader.read(pos, min(CHUNK, offset - pos))
        if not chunk:
            break
        line += chunk.count(b"\n")
        pos += len(chunk)
    return line


def _cell_crop(text: str, start: int, width: int | None = None) -> tuple[str, bool]:
    """The substring covering display cells [start, start+width) — to the end
    of the string if width is None. Second value: whether anything to the
    right was cut off."""
    pos = 0
    col = 0
    n = len(text)
    while pos < n and col < start:
        col += cell_len(text[pos])
        pos += 1
    if width is None:
        return text[pos:], False
    out: list[str] = []
    used = 0
    while pos < n:
        w = cell_len(text[pos])
        if used + w > width:
            return "".join(out), True
        out.append(text[pos])
        used += w
        pos += 1
    return "".join(out), False


class TextPager(Widget, can_focus=True):
    """Read-only viewer for a large file: scroll, page, jump, wrap toggle."""

    DEFAULT_CSS = """
    TextPager { background: $background; color: $foreground; }
    """

    BINDINGS = [
        Binding("down,ctrl+n", "scroll_lines(1)", "down", show=False),
        Binding("up,ctrl+p", "scroll_lines(-1)", "up", show=False),
        Binding("pagedown,ctrl+v,space", "scroll_page(1)", "page down", show=False),
        Binding("pageup,alt+v", "scroll_page(-1)", "page up", show=False),
        Binding("g,ctrl+home", "goto_start", "top", show=False),
        Binding("G,ctrl+end", "goto_end", "bottom", show=False),
        Binding("right,ctrl+f", "scroll_h(8)", "right", show=False),
        Binding("left,ctrl+b", "scroll_h(-8)", "left", show=False),
        Binding("n", "search_next(1)", "next match", show=False),
        Binding("N", "search_next(-1)", "prev match", show=False),
        Binding("F", "follow", "follow", show=False),
        # Search / goto prompts live on the app (they push a minibuffer); the
        # pager only has focus, so bind the keys here and delegate up. C-s / C-r
        # repeat to the next / previous match (like the editor's isearch); `/`
        # and `?` always start a fresh search.
        Binding("ctrl+s", "app.isearch_forward", "next match", show=False),
        Binding("ctrl+r", "app.isearch_backward", "prev match", show=False),
        Binding("slash", "app.pager_search_new(1)", "search", show=False),
        Binding("question_mark", "app.pager_search_new(-1)", "search back", show=False),
        Binding("alt+g", "app.pager_goto_line", "goto line", show=False),
        Binding("e,v", "app.pager_open_in_editor", "open in editor", show=False),
    ]

    class Moved(Message):
        """Posted when the viewport position changes (for the status bar)."""

    def __init__(self, path: Path | None = None, wrap: bool = False, **kwargs) -> None:
        super().__init__(**kwargs)
        self.path = path
        self._wrap = wrap
        self._reader: _FileReader | None = None
        self._index = LineIndex()
        self._indexing = True
        # Bumped whenever the pager points elsewhere; workers carry the value
        # they were started with, so a stale open/search can't install results.
        self._generation = 0
        self._search_seq = 0
        self._search_running = False
        self._follow = False
        self._follow_timer = None
        self._row_cache: OrderedDict[tuple[int, int], list[str]] = OrderedDict()
        self.top_line = 0
        self.top_seg = 0
        self.hoffset = 0
        self._last_query = ""
        self.match_line: int | None = None
        self._match_byte: int | None = None

    # -- lifecycle -----------------------------------------------------------

    def on_mount(self) -> None:
        if self.path is not None and self._reader is None:
            self._start_open(self.path)

    def load(self, path: Path) -> None:
        """(Re)point the pager at a file — for lazy use inside a pane."""
        self._start_open(path)

    def _start_open(self, path: Path) -> None:
        self._generation += 1
        self._search_seq += 1
        self._search_running = False
        self.stop_follow()
        self.path = path
        self._close_reader()
        self._index = LineIndex()
        self._indexing = True
        self._row_cache.clear()
        self.top_line = self.top_seg = self.hoffset = 0
        self._last_query = ""
        self.match_line = None
        self._match_byte = None
        self.refresh()
        self.run_worker(
            partial(self._open_worker, path, self._generation),
            thread=True,
            exclusive=True,
            group="pager-open",
        )

    def _open_worker(self, path: Path, gen: int) -> None:
        worker = get_current_worker()
        try:
            reader = _FileReader(path)
        except OSError:
            self.app.call_from_thread(self._install, gen, None, LineIndex())
            return
        index = LineIndex()
        if not index.scan(
            reader, should_stop=lambda: worker.is_cancelled or gen != self._generation
        ):
            reader.close()
            return
        self.app.call_from_thread(self._install, gen, reader, index)

    def _install(self, gen: int, reader: _FileReader | None, index: LineIndex) -> None:
        if gen != self._generation:  # a newer load/unload superseded this one
            if reader is not None:
                reader.close()
            return
        self._close_reader()
        self._reader = reader
        self._index = index
        self._indexing = False
        self._moved()

    def _close_reader(self) -> None:
        if self._reader is not None:
            self._reader.close()
            self._reader = None

    def unload(self) -> None:
        """Detach from the file (leaving pager mode): invalidate any running
        workers, stop following and release the file handle."""
        self._generation += 1
        self._search_seq += 1
        self._search_running = False
        self.stop_follow()
        self._close_reader()
        self.path = None
        self._index = LineIndex()
        self._indexing = True
        self._row_cache.clear()
        self._last_query = ""
        self.match_line = None
        self._match_byte = None

    def on_unmount(self) -> None:
        self._generation += 1
        self._search_seq += 1
        self.stop_follow()
        self._close_reader()

    @property
    def line_count(self) -> int:
        return self._index.line_count

    def status(self) -> str:
        """Short position string for the app status bar."""
        if self._indexing:
            return "indexing…"
        total = self._index.line_count
        pct = 0 if total <= 1 else round(self.top_line * 100 / (total - 1))
        wrap = "wrap" if self._wrap else "no-wrap"
        extra = "  searching…" if self._search_running else ""
        if self._follow:
            extra += "  FOLLOW"
        return f"Ln {self.top_line + 1}/{total}  {pct}%  {wrap}{extra}"

    def _moved(self) -> None:
        self.refresh()
        self.post_message(self.Moved())

    def action_search_next(self, direction: int) -> None:
        if self._last_query:
            self.search_next(direction > 0)

    @property
    def wrap(self) -> bool:
        return self._wrap

    @property
    def searching(self) -> bool:
        """Whether a query is active (so C-s should repeat rather than prompt)."""
        return bool(self._last_query)

    # -- reading -------------------------------------------------------------

    def _line_start(self, line: int) -> int:
        """Byte offset where `line` begins, via the sparse index."""
        reader = self._reader
        if reader is None:
            return 0
        block = min(line // SPARSE_STEP, len(self._index.offsets) - 1)
        current = block * SPARSE_STEP
        pos = self._index.offsets[block]
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

    def read_line(self, line: int) -> str:
        """The line's text — read at most MAX_LINE_BYTES deep, so a
        pathologically long line never costs more than that."""
        return self._read_line_capped(line)[0]

    def _read_line_capped(self, line: int) -> tuple[str, bool]:
        """(text, was_cut): the line up to MAX_LINE_BYTES."""
        if self._reader is None or line < 0 or line >= self._index.line_count:
            return "", False
        start = self._line_start(line)
        raw = self._reader.read(start, MAX_LINE_BYTES + 1)
        nl = raw.find(b"\n")
        if nl != -1:
            return raw[:nl].decode("utf-8", "replace"), False
        cut = len(raw) > MAX_LINE_BYTES
        if cut:
            raw = raw[:MAX_LINE_BYTES]
        return raw.decode("utf-8", "replace"), cut

    def _line_rows(self, line: int, width: int) -> list[str]:
        """Display rows for one file line at this width: tabs expanded and,
        when wrapping, split cell-accurately (CJK/emoji safe). Cached, since
        scrolling and rendering revisit the same lines."""
        key = (line, width if self._wrap else -1)
        cache = self._row_cache
        if key in cache:
            cache.move_to_end(key)
            return cache[key]
        text, cut = self._read_line_capped(line)
        text = text.expandtabs(TABSTOP)
        if not self._wrap or width <= 0:
            rows = [text]
        else:
            rows = chop_cells(text, width) or [""]
            if cut:
                rows[-1] += " …"  # the line continues past the display cap
        cache[key] = rows
        if len(cache) > 256:
            cache.popitem(last=False)
        return rows

    def _seg_count(self, line: int, width: int) -> int:
        if not self._wrap:
            return 1
        return len(self._line_rows(line, width))

    # -- viewport ------------------------------------------------------------

    def _viewport_rows(self, height: int, width: int) -> list[str]:
        rows: list[str] = []
        line = self.top_line
        seg = self.top_seg
        while len(rows) < height and line < self._index.line_count:
            for piece in self._line_rows(line, width)[seg:]:
                if not self._wrap and self.hoffset:
                    piece = _cell_crop(piece, self.hoffset)[0]
                rows.append(piece)
                if len(rows) >= height:
                    break
            line += 1
            seg = 0
        while len(rows) < height:
            rows.append("")
        return rows

    HIGHLIGHT = Style(bgcolor="yellow", color="black")

    def render(self) -> Text:
        if self._indexing:
            return Text(" indexing…", style="dim")
        width = self.size.width
        height = self.size.height
        query = self._last_query
        fold = bool(query) and query == query.lower()  # smart case, as search
        out = Text(no_wrap=True, end="")
        for i, row in enumerate(self._viewport_rows(height, width)):
            if i:
                out.append("\n")
            # Hard-crop each row to the width (in cells, so wide characters
            # count double); mark a cut line with a chevron.
            if width > 0 and (len(row) > width or cell_len(row) > width):
                row = _cell_crop(row, 0, width - 1)[0] + "›"
            if query:
                self._append_highlighted(out, row, query, fold)
            else:
                out.append(row)
        return out

    def _append_highlighted(self, out: Text, row: str, query: str, fold: bool) -> None:
        """Append `row`, styling every occurrence of the query (all matches in
        the visible area, not just the current one)."""
        hay = row.lower() if fold else row
        needle = query.lower() if fold else query
        n = len(needle)
        start = 0
        while n:
            idx = hay.find(needle, start)
            if idx == -1:
                break
            out.append(row[start:idx])
            out.append(row[idx : idx + n], self.HIGHLIGHT)
            start = idx + n
        out.append(row[start:])

    # -- navigation ----------------------------------------------------------

    def _step_down(self) -> bool:
        """Advance the top by one display row; returns False at EOF."""
        width = self.size.width
        if self.top_seg + 1 < self._seg_count(self.top_line, width):
            self.top_seg += 1
            return True
        if self.top_line + 1 < self._index.line_count:
            self.top_line += 1
            self.top_seg = 0
            return True
        return False

    def _step_up(self) -> bool:
        if self.top_seg > 0:
            self.top_seg -= 1
            return True
        if self.top_line > 0:
            self.top_line -= 1
            self.top_seg = self._seg_count(self.top_line, self.size.width) - 1
            return True
        return False

    def action_scroll_lines(self, amount: int) -> None:
        step = self._step_down if amount > 0 else self._step_up
        for _ in range(abs(amount)):
            if not step():
                break
        self._moved()

    def action_scroll_page(self, direction: int) -> None:
        self.action_scroll_lines(direction * max(1, self.size.height - 2))

    def action_scroll_h(self, amount: int) -> None:
        if not self._wrap:
            self.hoffset = max(0, self.hoffset + amount)
            self._moved()

    def action_goto_start(self) -> None:
        self.top_line = self.top_seg = self.hoffset = 0
        self._moved()

    def action_goto_end(self) -> None:
        self.top_line = max(0, self._index.line_count - 1)
        self.top_seg = 0
        # back up so the last line sits at the bottom of the viewport
        self.action_scroll_lines(-(self.size.height - 1))

    def goto_line(self, line: int) -> None:
        self.top_line = max(0, min(line, self._index.line_count - 1))
        self.top_seg = 0
        self._moved()

    def goto_percent(self, pct: float) -> None:
        pct = max(0.0, min(100.0, pct))
        self.goto_line(int((self._index.line_count - 1) * pct / 100))

    # -- follow mode -----------------------------------------------------------

    @property
    def following(self) -> bool:
        return self._follow

    def action_follow(self) -> None:
        """F: stick to the end of a growing file (like `less +F`); any key
        stops following."""
        if self._follow:
            self.stop_follow()
            self.app.notify("Follow: off", timeout=1.5)
            return
        if self._reader is None:
            return
        self._follow = True
        self._follow_timer = self.set_interval(FOLLOW_INTERVAL, self._poll_follow)
        self._poll_follow()
        self.app.notify("Follow mode — any key stops", timeout=2)

    def stop_follow(self) -> None:
        if self._follow_timer is not None:
            self._follow_timer.stop()
            self._follow_timer = None
        if self._follow:
            self._follow = False
            self._moved()

    def _poll_follow(self) -> None:
        reader = self._reader
        if reader is None or self.path is None:
            return
        try:
            disk = os.stat(self.path)
            rotated = disk.st_ino != os.fstat(reader.fd).st_ino
        except OSError:
            return  # file temporarily missing (mid-rotation): try again later
        if rotated or disk.st_size < self._index.scanned:
            # Replaced or truncated: reopen from scratch, keep following.
            self.app.notify("File rotated/truncated — reloading", timeout=2)
            self.load(self.path)
            self._follow = True
            self._follow_timer = self.set_interval(FOLLOW_INTERVAL, self._poll_follow)
            return
        if reader.refresh_size() > self._index.scanned:
            # Index only the new tail, bounded per poll to keep the UI live.
            already = self._index.scanned
            self._index.scan(
                reader,
                should_stop=lambda: self._index.scanned - already > FOLLOW_SCAN_BUDGET,
            )
            self._row_cache.clear()  # the previously-last line may have grown
            self.action_goto_end()

    # -- streaming search ----------------------------------------------------

    def search(self, query: str, forward: bool = True) -> None:
        self._last_query = query
        self._match_byte = None  # fresh query: match from the current viewport
        self.search_next(forward)

    def search_next(self, forward: bool = True) -> None:
        """Scan to the next/previous match of the last query on a worker
        thread (smart case, wraps around; C-g cancels). Advances past the
        current match, so repeated matches on one line are each reachable."""
        if not self._last_query:
            return
        if self._reader is None:
            if self._indexing:
                self.app.notify("Still indexing…", timeout=2)
            return
        query = self._last_query
        pattern = smartcase_pattern(query)
        overlap = _match_overlap(query)
        # Anchor to the current match when we're still on its line, so C-s
        # moves to the *next* occurrence rather than re-finding the current
        # one; after a scroll, anchor to the top of the viewport instead.
        anchored = self._match_byte is not None and self.match_line == self.top_line
        line_start = self._line_start(self.top_line)
        if forward:
            anchor = self._match_byte + 1 if anchored else line_start
        else:
            anchor = self._match_byte if anchored else line_start
        self._search_seq += 1
        self._search_running = True
        self._moved()  # show "searching…" in the status bar
        self.run_worker(
            partial(
                self._search_worker,
                self._reader.dup(),
                self._index,
                pattern,
                forward,
                anchor,
                overlap,
                query,
                self._generation,
                self._search_seq,
            ),
            thread=True,
            exclusive=True,
            group="pager-search",
        )

    def _search_worker(
        self, reader, index, pattern, forward, anchor, overlap, query, gen, seq
    ) -> None:
        worker = get_current_worker()

        def stop() -> bool:
            return (
                worker.is_cancelled
                or gen != self._generation
                or seq != self._search_seq
            )

        try:
            size = reader.size
            if forward:
                offset = _scan_forward(reader, pattern, anchor, size, overlap, stop)
                if offset == -1:  # wrap around: matches starting before the anchor
                    offset = _scan_forward(reader, pattern, 0, anchor, overlap, stop)
            else:
                offset = _scan_backward(reader, pattern, anchor, overlap, stop)
                if offset == -1:  # wrap around to the end of the file
                    offset = _scan_backward(reader, pattern, size, overlap, stop)
            if offset is None:  # cancelled
                return
            line = _count_line(reader, index, offset) if offset >= 0 else -1
        finally:
            reader.close()
        self.app.call_from_thread(self._search_done, gen, seq, query, offset, line)

    def _search_done(self, gen: int, seq: int, query: str, offset: int, line: int) -> None:
        if gen != self._generation or seq != self._search_seq:
            return
        self._search_running = False
        if offset < 0:
            # Miss: drop the query, so the highlight doesn't linger on a term
            # that isn't there and the next C-s prompts for a fresh one.
            self._last_query = ""
            self._match_byte = None
            self.match_line = None
            self.app.notify(f"Not found: {query}", severity="warning", timeout=2)
            self._moved()
            return
        self._match_byte = offset
        self.match_line = line
        self.top_line = line
        self.top_seg = 0
        self.hoffset = 0
        self._moved()

    def cancel_search(self) -> None:
        """Exit search (C-g / Escape): stop any running scan, drop the query
        and clear the highlight, so the next C-s prompts for a fresh term."""
        self._search_seq += 1  # a running worker sees a stale seq and stops
        self._search_running = False
        self._last_query = ""
        self._match_byte = None
        self.match_line = None
        self._moved()

    def toggle_wrap(self) -> bool:
        self._wrap = not self._wrap
        self.top_seg = 0
        self.hoffset = 0
        self._row_cache.clear()
        self._moved()
        return self._wrap

    def on_key(self, event: events.Key) -> None:
        if self._follow and event.key != "F":
            # Any key stops following (and then acts normally).
            self.stop_follow()
            self.app.notify("Follow: off", timeout=1.5)
        # w toggles wrap locally (the app also wires C-x w).
        if event.key == "w":
            event.stop()
            self.toggle_wrap()
        elif event.key == "escape" and self.searching:
            # Escape (like C-g) exits search and clears the highlight.
            event.stop()
            self.cancel_search()
