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

import time

from rich.console import Console
from rich.markdown import Markdown as RichMarkdown
from textual.geometry import Size
from textual.scroll_view import ScrollView
from textual.strip import Strip

from .markdown import FENCE_RE

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


def render_strips(text: str, width: int) -> list[Strip]:
    """Render markdown to per-line strips at a fixed width (thread-safe,
    called from a worker thread)."""
    console = Console(
        width=max(20, width),
        force_terminal=True,
        color_system="truecolor",
        highlight=False,
    )
    lines = console.render_lines(RichMarkdown(text, code_theme="default"), pad=True)
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


class MarkdownPreview(ScrollView, can_focus=True):
    """A scrollable rendered-markdown view of the buffer's text."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._strips: list[Strip] = []
        self._text: str | None = None
        self._generation = 0
        self._busy = False
        self._rendered_width = 0

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
        self._strips = strips
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
            return Strip.blank(self.size.width)
        return self._strips[row].crop(scroll_x, scroll_x + self.size.width)

    def plain_text(self) -> str:
        """The rendered preview as plain text (for tests and debugging)."""
        return "\n".join(strip.text for strip in self._strips)


# Preview modes cycled by C-c C-v, applied as CSS classes on the buffer pane.
PREVIEW_MODES = ("split", "only", "off")
PREVIEW_CLASSES = {"split": "-preview-split", "only": "-preview-only"}
