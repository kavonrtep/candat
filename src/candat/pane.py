"""BufferPane: one tab holding an editor plus its markdown-preview and CSV
siblings, and owning the view-mode state (preview split/only/off, CSV table
vs. text). Keeping this here means the app talks to a pane through methods
instead of reaching into `pane.query_one(...)` and juggling CSS classes.
"""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widget import Widget
from textual.widgets import TabPane

from .csvview import CsvViewer
from .editor import EditorBuffer
from .pager import TextPager
from .preview import PREVIEW_CLASSES, MarkdownPreview

CSV_CLASS = "-csv-table"
PAGER_CLASS = "-pager"


class BufferPane(TabPane):
    def __init__(self, editor: EditorBuffer, pane_id: str) -> None:
        super().__init__(editor.display_name, id=pane_id)
        self._editor = editor

    def compose(self) -> ComposeResult:
        yield Horizontal(self._editor, MarkdownPreview(), CsvViewer(), TextPager())

    # -- children ------------------------------------------------------------

    @property
    def editor(self) -> EditorBuffer:
        return self._editor

    @property
    def preview(self) -> MarkdownPreview:
        return self.query_one(MarkdownPreview)

    @property
    def csv(self) -> CsvViewer:
        return self.query_one(CsvViewer)

    @property
    def pager(self) -> TextPager:
        return self.query_one(TextPager)

    @property
    def visible_widget(self) -> Widget:
        """The widget a user interacts with: table in CSV mode, pager in pager
        mode, else the editor."""
        if self.is_csv:
            return self.csv.table
        if self.is_pager:
            return self.pager
        return self._editor

    def focus_visible(self) -> None:
        self.visible_widget.focus()

    # -- markdown preview ----------------------------------------------------

    @property
    def preview_mode(self) -> str:
        if self.has_class("-preview-only"):
            return "only"
        if self.has_class("-preview-split"):
            return "split"
        return "off"

    async def set_preview_mode(self, mode: str) -> None:
        self.remove_class(*PREVIEW_CLASSES.values())
        if mode in PREVIEW_CLASSES:
            self.add_class(PREVIEW_CLASSES[mode])
        if mode != "off":
            await self.preview.render_text(self._editor.text)
        (self.preview if mode == "only" else self._editor).focus()

    def sync_preview_scroll(self) -> None:
        """Scroll the preview to the same relative position as the editor."""
        editor = self._editor
        if self.preview_mode != "split" or editor.max_scroll_y <= 0:
            return
        preview = self.preview
        fraction = editor.scroll_y / editor.max_scroll_y
        preview.scroll_to(y=fraction * preview.max_scroll_y, animate=False)

    # -- CSV table -----------------------------------------------------------

    @property
    def is_csv(self) -> bool:
        return self.has_class(CSV_CLASS)

    def enter_csv_mode(self, path: Path) -> None:
        self.csv.open_file(path)
        self.show_table()

    def show_table(self) -> None:
        """Show the (already-loaded) table without re-reading the file."""
        self.add_class(CSV_CLASS)
        self.csv.table.focus()

    def leave_csv_mode(self) -> None:
        self.remove_class(CSV_CLASS)

    # -- large-file pager ----------------------------------------------------

    @property
    def is_pager(self) -> bool:
        return self.has_class(PAGER_CLASS)

    def enter_pager_mode(self, path: Path) -> None:
        self.pager.load(path)
        self.add_class(PAGER_CLASS)
        # Focus after the class makes the pager display:block — focusing a
        # still-hidden widget is a no-op, leaving the editor focused (so C-s
        # would hit the editor's isearch instead of the pager's).
        self.call_after_refresh(self.pager.focus)


def pane_of(widget: Widget | None) -> BufferPane | None:
    """The BufferPane containing a widget (e.g. an editor), or None."""
    if widget is None:
        return None
    for ancestor in widget.ancestors_with_self:
        if isinstance(ancestor, BufferPane):
            return ancestor
    return None
