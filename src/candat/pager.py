"""A windowed, `less`-style pager for very large files.

The file stays on disk (mmapped); a *sparse* line-offset index (one byte
offset every ``SPARSE_STEP`` lines) gives random access to any line without
holding the whole file in memory. Only the visible viewport is read and
rendered. Navigation is anchored to file lines; soft wrap is computed for the
viewport alone, so a huge file with long lines never needs a full wrap pass.
"""

from __future__ import annotations

import mmap
from pathlib import Path

from rich.text import Text
from textual import events
from textual.binding import Binding
from textual.widget import Widget

SPARSE_STEP = 512  # index a byte offset every 512 lines


class LineIndex:
    """Sparse map from line number to byte offset, plus the total line count."""

    def __init__(self) -> None:
        self.offsets: list[int] = [0]  # offsets[k] = start of line k*SPARSE_STEP
        self.line_count = 0
        self.complete = False

    def build(self, data: mmap.mmap) -> None:
        size = len(data)
        count = 0
        pos = 0
        find = data.find
        while pos < size:
            nl = find(b"\n", pos)
            if nl == -1:
                count += 1  # trailing line with no newline
                break
            count += 1
            pos = nl + 1
            if count % SPARSE_STEP == 0:
                self.offsets.append(pos)
        self.line_count = count
        self.complete = True


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
    ]

    def __init__(self, path: Path, wrap: bool = False, **kwargs) -> None:
        super().__init__(**kwargs)
        self.path = path
        self._wrap = wrap
        self._file = None
        self._mm: mmap.mmap | None = None
        self._index = LineIndex()
        self._indexing = True
        self.top_line = 0
        self.top_seg = 0
        self.hoffset = 0

    # -- lifecycle -----------------------------------------------------------

    def open(self) -> None:
        """Open + mmap the file and build the index. Safe to call off-thread
        except the mmap open, which is cheap."""
        size = self.path.stat().st_size
        if size == 0:
            self._index.complete = True
            self._indexing = False
            return
        self._file = open(self.path, "rb")
        self._mm = mmap.mmap(self._file.fileno(), 0, access=mmap.ACCESS_READ)
        self._index.build(self._mm)
        self._indexing = False

    def on_mount(self) -> None:
        self.run_worker(self._open_worker, thread=True, exclusive=True)

    def _open_worker(self) -> None:
        self.open()
        self.app.call_from_thread(self.refresh)

    def on_unmount(self) -> None:
        if self._mm is not None:
            self._mm.close()
        if self._file is not None:
            self._file.close()

    @property
    def line_count(self) -> int:
        return self._index.line_count

    @property
    def wrap(self) -> bool:
        return self._wrap

    # -- reading -------------------------------------------------------------

    def _line_start(self, line: int) -> int:
        """Byte offset where `line` begins, via the sparse index."""
        assert self._mm is not None
        block = min(line // SPARSE_STEP, len(self._index.offsets) - 1)
        pos = self._index.offsets[block]
        current = block * SPARSE_STEP
        find = self._mm.find
        while current < line:
            nl = find(b"\n", pos)
            if nl == -1:
                return len(self._mm)
            pos = nl + 1
            current += 1
        return pos

    def read_line(self, line: int) -> str:
        if self._mm is None or line < 0 or line >= self._index.line_count:
            return ""
        start = self._line_start(line)
        end = self._mm.find(b"\n", start)
        if end == -1:
            end = len(self._mm)
        return self._mm[start:end].decode("utf-8", "replace")

    def _segments(self, text: str, width: int) -> list[str]:
        """Display rows a file line occupies at this width."""
        if not self._wrap or width <= 0:
            return [text]
        return [text[i : i + width] for i in range(0, len(text), width)] or [""]

    def _seg_count(self, line: int, width: int) -> int:
        if not self._wrap:
            return 1
        length = len(self.read_line(line))
        return max(1, -(-length // width)) if width > 0 else 1

    # -- viewport ------------------------------------------------------------

    def _viewport_rows(self, height: int, width: int) -> list[str]:
        rows: list[str] = []
        line = self.top_line
        seg = self.top_seg
        while len(rows) < height and line < self._index.line_count:
            for piece in self._segments(self.read_line(line), width)[seg:]:
                rows.append(piece if self._wrap else piece[self.hoffset :])
                if len(rows) >= height:
                    break
            line += 1
            seg = 0
        while len(rows) < height:
            rows.append("")
        return rows

    def render(self) -> Text:
        if self._indexing:
            return Text(" indexing…", style="dim")
        width = self.size.width
        height = self.size.height
        rows = self._viewport_rows(height, width)
        return Text("\n".join(rows), no_wrap=True, overflow="crop", end="")

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
        self.refresh()

    def action_scroll_page(self, direction: int) -> None:
        self.action_scroll_lines(direction * max(1, self.size.height - 2))

    def action_scroll_h(self, amount: int) -> None:
        if not self._wrap:
            self.hoffset = max(0, self.hoffset + amount)
            self.refresh()

    def action_goto_start(self) -> None:
        self.top_line = self.top_seg = self.hoffset = 0
        self.refresh()

    def action_goto_end(self) -> None:
        self.top_line = max(0, self._index.line_count - 1)
        self.top_seg = 0
        # back up so the last line sits at the bottom of the viewport
        self.action_scroll_lines(-(self.size.height - 1))

    def goto_line(self, line: int) -> None:
        self.top_line = max(0, min(line, self._index.line_count - 1))
        self.top_seg = 0
        self.refresh()

    def toggle_wrap(self) -> bool:
        self._wrap = not self._wrap
        self.top_seg = 0
        self.hoffset = 0
        self.refresh()
        return self._wrap

    def on_key(self, event: events.Key) -> None:
        # w toggles wrap locally (the app also wires C-x w).
        if event.key == "w":
            event.stop()
            self.toggle_wrap()
