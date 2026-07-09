"""Live markdown preview panel, shown beside the editor in a buffer pane."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.css.query import NoMatches
from textual.widgets import Markdown


class MarkdownPreview(VerticalScroll):
    """A scrollable rendered-markdown view of the buffer's text."""

    can_focus = True

    def compose(self) -> ComposeResult:
        yield Markdown()

    async def render_text(self, text: str) -> None:
        try:
            markdown = self.query_one(Markdown)
        except NoMatches:
            return  # not mounted yet; a later refresh will render
        await markdown.update(text)


# Preview modes cycled by C-c C-v, applied as CSS classes on the buffer pane.
PREVIEW_MODES = ("split", "only", "off")
PREVIEW_CLASSES = {"split": "-preview-split", "only": "-preview-only"}
