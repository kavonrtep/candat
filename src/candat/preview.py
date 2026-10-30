"""Live markdown preview panel, shown beside the editor in a buffer pane.

The document is rendered *off* the UI thread: a worker turns the markdown
into per-line strips with rich's renderer, and the widget only blits the
visible lines (Textual's line API), so re-rendering costs the UI thread a
few milliseconds no matter how big the document is. Textual's Markdown
widget, by contrast, builds a widget tree for every block on the UI thread
— ~30 s for a 130 KB document — which froze the whole app while typing
(and could exhaust memory on very large files).

While a render is in flight, newer text just bumps the generation; the
worker's result is dropped if stale and the latest text is rendered next,
so a burst of edits coalesces instead of queueing a render per keystroke.
"""

from __future__ import annotations

import re
import time

from rich.cells import cell_len
from rich.console import Console
from rich.markdown import Markdown as RichMarkdown
from rich.segment import Segment
from rich.style import Style
from rich.theme import Theme
from textual import events
from textual.binding import Binding
from textual.geometry import Region, Size
from textual.message import Message
from textual.scroll_view import ScrollView
from textual.strip import Strip

from . import clipboard
from .codeblocks import fence_language
from .markdown import FENCE_RE
from .theme import CODE_BLOCK_BG

# Documents beyond this stop live-rendering entirely; rich's renderer is
# linear (~4 s/MB in a background thread) but there is no point burning CPU
# on every keystroke for megabyte previews.
PREVIEW_MAX_BYTES = 1_000_000
FALLBACK_WIDTH = 78
# A big document renders in chunks so the worker can yield the GIL between
# them (a CPU-bound thread otherwise starves the UI thread — CPython's
# convoy effect) and abort early when the text has changed again. Chunks
# split at blank lines outside code fences, so blocks stay intact; only
# cross-chunk reference links lose fidelity, on large documents.
CHUNK_BYTES = 32_000
YIELD_SECONDS = 0.004

# Terminal italics are often hard to read, so emphasis is a colour instead
# (the raw editor keeps real italics, where the *markers* show it anyway).
PREVIEW_THEME = Theme(
    {
        "markdown.em": Style(color="#8250df", italic=False),
        "markdown.emph": Style(color="#8250df", italic=False),
        "markdown.h4": Style(color="#8250df", bold=True, italic=False),
        "markdown.h5": Style(bold=True, italic=False),
        "markdown.h7": Style(dim=True, italic=False),
        # rich's default is bold cyan on black: match the raw editor instead.
        "markdown.code": Style(color="#0a3069", bgcolor=CODE_BLOCK_BG, bold=False),
    }
)
SELECTION_STYLE = Style(bgcolor="#c8c8fa")
CURSOR_STYLE = Style(reverse=True)


# R Markdown chunk headers (```{r, echo=FALSE}) -> a plain ```r rich knows.
_RMD_FENCE = re.compile(r"^(\s{0,3}(?:`{3,}|~{3,}))\{([^}]*)\}.*$", re.MULTILINE)


def _plain_fences(text: str) -> str:
    if "{" not in text:
        return text
    return _RMD_FENCE.sub(lambda m: m[1] + (fence_language(m[2]) or ""), text)


def render_strips(text: str, width: int) -> list[Strip]:
    """Render markdown to per-line strips at a fixed width (thread-safe,
    called from a worker thread)."""
    console = Console(
        width=max(20, width),
        force_terminal=True,
        color_system="truecolor",
        highlight=False,
        theme=PREVIEW_THEME,
    )
    lines = console.render_lines(
        RichMarkdown(_plain_fences(text), code_theme="default"), pad=True
    )
    return [Strip(segments, max(20, width)) for segments in lines]


def markdown_chunks(text: str) -> list[str]:
    """Split markdown into renderable chunks of roughly CHUNK_BYTES at blank
    lines outside code fences (a blank line legally terminates any block)."""
    if len(text) <= CHUNK_BYTES * 2:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    fences = 0
    for line in text.split("\n"):
        if not line.strip() and size >= CHUNK_BYTES and fences % 2 == 0:
            chunks.append("\n".join(current))
            current, size = [], 0
            continue  # the blank separator is re-added between chunks
        current.append(line)
        size += len(line) + 1
        if FENCE_RE.match(line):
            fences += 1
    if current:
        chunks.append("\n".join(current))
    return chunks


def overlay(strip: Strip, start: int, end: int, style: Style) -> Strip:
    """`strip` with `style` laid over cells [start, end)."""
    if start >= end:
        return strip
    length = strip.cell_length
    if end > length:  # a cursor past the text: pad so it shows
        strip = strip.extend_cell_length(end)
        length = end
    cuts = [c for c in (start, end, length) if 0 < c <= length]
    parts = strip.divide(sorted(set(cuts)))
    out: list[Segment] = []
    pos = 0
    for part in parts:
        if start <= pos < end:
            out.extend(
                Segment(text, (seg_style or Style()) + style, control)
                for text, seg_style, control in part
            )
        else:
            out.extend(part)
        pos += part.cell_length
    return Strip(out, length)


class MarkdownPreview(ScrollView, can_focus=True):
    """A scrollable rendered-markdown view of the buffer's text, with a
    read-only cursor: move it, set a mark (or shift-move, or drag the mouse)
    and copy the selection with M-w / C-w."""

    BINDINGS = [
        Binding("down,ctrl+n", "move(1, 0)", show=False),
        Binding("up,ctrl+p", "move(-1, 0)", show=False),
        Binding("right,ctrl+f", "move(0, 1)", show=False),
        Binding("left,ctrl+b", "move(0, -1)", show=False),
        Binding("shift+down", "move(1, 0, True)", show=False),
        Binding("shift+up", "move(-1, 0, True)", show=False),
        Binding("shift+right", "move(0, 1, True)", show=False),
        Binding("shift+left", "move(0, -1, True)", show=False),
        Binding("home,ctrl+a", "line_edge(False)", show=False),
        Binding("end,ctrl+e", "line_edge(True)", show=False),
        Binding("pagedown,ctrl+v,space", "page(1)", show=False),
        Binding("pageup,alt+v", "page(-1)", show=False),
        Binding("ctrl+home,alt+less_than_sign", "edge(False)", show=False),
        Binding("ctrl+end,alt+greater_than_sign", "edge(True)", show=False),
        Binding("ctrl+@", "set_mark", show=False),
        Binding("alt+w,ctrl+w", "copy", show=False),
    ]
    # ESC as a one-shot Meta prefix (ESC w == M-w), as in the editor.
    META_KEYS = {
        "w": "copy",
        "v": "page(-1)",
        "less_than_sign": "edge(False)",
        "greater_than_sign": "edge(True)",
    }

    class Busy(Message):
        """A render started (busy=True) or finished (busy=False); the app
        shows a status-bar indicator for renders that take a while."""

        def __init__(self, preview: "MarkdownPreview", busy: bool) -> None:
            super().__init__()
            self.preview = preview
            self.busy = busy

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._strips: list[Strip] = []
        self._text: str | None = None
        self._generation = 0
        self._busy = False
        self._rendered_width = 0
        self.cursor: tuple[int, int] = (0, 0)  # (row, char column)
        self.mark: tuple[int, int] | None = None
        self._lines: list[str] = []  # plain text per row, trailing pad cut
        self._meta = False
        self._dragging = False

    async def render_text(self, text: str) -> None:
        """Show `text` (eventually): schedules a background render and
        returns immediately; stale results are dropped."""
        self._text = text
        self._generation += 1
        if not self._busy:
            self._start_render()

    def _start_render(self) -> None:
        if self._text is None:
            return
        self._busy = True
        self.post_message(self.Busy(self, True))
        generation = self._generation
        text = self._text
        width = self.scrollable_content_region.width or FALLBACK_WIDTH
        if len(text) > PREVIEW_MAX_BYTES:
            text = (
                f"*Preview disabled — the document is "
                f"{len(text) / 1_000_000:.1f} MB (limit 1 MB). "
                f"`C-c C-v` cycles the preview off.*"
            )
        self._rendered_width = width

        def work() -> None:
            strips: list[Strip] = []
            for i, chunk in enumerate(markdown_chunks(text)):
                if generation != self._generation:
                    break  # stale: stop burning CPU, _finish restarts
                if i:
                    strips.append(Strip.blank(width))
                    time.sleep(YIELD_SECONDS)  # let the UI thread breathe
                strips.extend(render_strips(chunk, width))
            self.app.call_from_thread(self._finish, generation, strips, width)

        self.run_worker(work, thread=True, group="md-preview")

    def _finish(self, generation: int, strips: list[Strip], width: int) -> None:
        if not self.is_mounted:
            return
        self._busy = False
        if generation != self._generation:
            self._start_render()  # newer text arrived while rendering
            return
        self.post_message(self.Busy(self, False))
        self._strips = strips
        self._lines = [strip.text.rstrip() for strip in strips]
        self.cursor = self._clamp(*self.cursor)
        if self.mark is not None:
            self.mark = self._clamp(*self.mark)
        self.virtual_size = Size(width, len(strips))
        self.refresh()

    def _on_resize(self, event) -> None:
        # Re-render at the new width (the strips are pre-wrapped).
        width = self.scrollable_content_region.width
        if width and width != self._rendered_width and self._text is not None:
            self._generation += 1
            if not self._busy:
                self._start_render()

    def render_line(self, y: int) -> Strip:
        scroll_x, scroll_y = self.scroll_offset
        row = y + scroll_y
        if row >= len(self._strips):
            return Strip.blank(self.size.width, self.rich_style)
        strip = self._strips[row]
        if (span := self._selection_cells(row)) is not None:
            strip = overlay(strip, *span, SELECTION_STYLE)
        if self.has_focus and row == self.cursor[0]:
            cell = self._cell(row, self.cursor[1])
            strip = overlay(strip, cell, cell + 1, CURSOR_STYLE)
        # Unstyled cells take the app theme, not the terminal's defaults.
        return strip.crop(scroll_x, scroll_x + self.size.width).apply_style(
            self.rich_style
        )

    # -- cursor and selection ----------------------------------------------

    def _clamp(self, row: int, col: int) -> tuple[int, int]:
        if not self._lines:
            return 0, 0
        row = max(0, min(row, len(self._lines) - 1))
        return row, max(0, min(col, len(self._lines[row])))

    def _cell(self, row: int, col: int) -> int:
        """Cell offset of char column `col` (wide characters take two)."""
        line = self._lines[row] if row < len(self._lines) else ""
        return cell_len(line[:col]) + max(0, col - len(line))

    def _col_at_cell(self, row: int, cell: int) -> int:
        line = self._lines[row] if row < len(self._lines) else ""
        width = 0
        for i, char in enumerate(line):
            width += cell_len(char)
            if width > cell:
                return i
        return len(line)

    def _ordered(self) -> tuple[tuple[int, int], tuple[int, int]] | None:
        if self.mark is None or self.mark == self.cursor:
            return None
        start, end = sorted((self.mark, self.cursor))
        return start, end

    def _selection_cells(self, row: int) -> tuple[int, int] | None:
        span = self._ordered()
        if span is None:
            return None
        (r0, c0), (r1, c1) = span
        if not r0 <= row <= r1:
            return None
        start = self._cell(row, c0) if row == r0 else 0
        if row == r1:
            end = self._cell(row, c1)
        else:  # a selected line break shows as one cell past the text
            end = self._cell(row, len(self._lines[row])) + 1
        return start, end

    @property
    def selected_text(self) -> str:
        span = self._ordered()
        if span is None:
            return ""
        (r0, c0), (r1, c1) = span
        if r0 == r1:
            return self._lines[r0][c0:c1]
        parts = [self._lines[r0][c0:], *self._lines[r0 + 1 : r1], self._lines[r1][:c1]]
        return "\n".join(parts)

    def move_cursor(self, row: int, col: int, *, select: bool = False) -> None:
        if select and self.mark is None:
            self.mark = self.cursor
        self.cursor = self._clamp(row, col)
        self._keep_cursor_visible()
        self.refresh()

    def _keep_cursor_visible(self) -> None:
        row, col = self.cursor
        self.scroll_to_region(
            Region(self._cell(row, col), row, 1, 1), animate=False, force=True
        )

    def action_move(self, rows: int, cols: int, select: bool = False) -> None:
        row, col = self.cursor
        if rows:
            row += rows
        elif cols < 0 and col == 0 and row > 0:
            row, col = row - 1, len(self._lines[row - 1])
        elif cols > 0 and self._lines and col >= len(self._lines[row]):
            if row + 1 < len(self._lines):
                row, col = row + 1, 0
        else:
            col += cols
        self.move_cursor(row, col, select=select)

    def action_line_edge(self, end: bool) -> None:
        row = self.cursor[0]
        col = len(self._lines[row]) if end and self._lines else 0
        self.move_cursor(row, col)

    def action_page(self, direction: int) -> None:
        self.move_cursor(self.cursor[0] + direction * max(1, self.size.height - 1), 0)

    def action_edge(self, end: bool) -> None:
        self.move_cursor(len(self._lines) - 1 if end else 0, 0)

    def action_set_mark(self) -> None:
        self.mark = self.cursor
        self.app.notify("Mark set", timeout=1)
        self.refresh()

    def clear_mark(self) -> bool:
        """Drop the selection; False when there was none (C-g)."""
        had = self.mark is not None
        self.mark = None
        self.refresh()
        return had

    def action_copy(self) -> None:
        text = self.selected_text
        if not text:
            self.app.notify("The region is empty", severity="warning", timeout=2)
            return
        self.app.kill_ring.push(text)
        clipboard.copy_explicit(self.app, text)
        self.clear_mark()

    def on_key(self, event: events.Key) -> None:
        if self._meta:
            self._meta = False
            if (action := self.META_KEYS.get(event.key)) is not None:
                event.stop()
                event.prevent_default()
                self.call_later(self.run_action, action)
            return
        if event.key == "escape":
            self._meta = True
            event.stop()

    def _row_col(self, event: events.MouseEvent) -> tuple[int, int]:
        offset = event.get_content_offset(self)
        x, y = (offset.x, offset.y) if offset is not None else (0, 0)
        row = y + int(self.scroll_offset.y)
        row = max(0, min(row, len(self._lines) - 1))
        return row, self._col_at_cell(row, x + int(self.scroll_offset.x))

    def on_mouse_down(self, event: events.MouseDown) -> None:
        if event.button != 1 or not self._lines:
            return
        self.focus()
        self.cursor = self._row_col(event)
        self.mark = self.cursor
        self._dragging = True
        self.capture_mouse()
        self.refresh()

    def on_mouse_move(self, event: events.MouseMove) -> None:
        if self._dragging:
            self.cursor = self._row_col(event)
            self.refresh()

    def on_mouse_up(self, event: events.MouseUp) -> None:
        if self._dragging:
            self._dragging = False
            self.capture_mouse(False)
            if self.mark == self.cursor:
                self.mark = None  # a plain click just places the cursor
            self.refresh()

    def _on_focus(self, event: events.Focus) -> None:
        self.refresh()

    def _on_blur(self, event: events.Blur) -> None:
        self.refresh()

    def plain_text(self) -> str:
        """The rendered preview as plain text (for tests and debugging)."""
        return "\n".join(strip.text for strip in self._strips)


# Preview modes cycled by C-c C-v, applied as CSS classes on the buffer pane.
PREVIEW_MODES = ("split", "only", "off")
PREVIEW_CLASSES = {"split": "-preview-split", "only": "-preview-only"}
