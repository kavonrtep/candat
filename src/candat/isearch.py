"""Emacs-style incremental search (C-s / C-r).

A transparent modal captures keystrokes and moves the editor's selection to
the current match live, minibuffer-style prompt at the bottom. Smart case:
an all-lowercase query is case-insensitive.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual import events
from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.widgets import Label
from textual.widgets.text_area import Selection

if TYPE_CHECKING:
    from .editor import EditorBuffer


class ISearchScreen(ModalScreen[None]):
    CSS = """
    ISearchScreen {
        align: left bottom;
        background: transparent;
    }
    ISearchScreen Label {
        height: 1;
        width: 100%;
        padding: 0 1;
        background: $panel;
        color: $foreground;
    }
    ISearchScreen Label.failing {
        color: $error;
    }
    """

    def __init__(self, editor: EditorBuffer, forward: bool = True) -> None:
        super().__init__()
        self._editor = editor
        self._forward = forward
        self._origin = editor.selection
        self._query = ""
        self._match: tuple[tuple[int, int], tuple[int, int]] | None = None
        self._failed = False
        self._wrapped = False

    def compose(self) -> ComposeResult:
        yield Label()

    def on_mount(self) -> None:
        self._show()

    # -- painting --------------------------------------------------------------

    def _show(self) -> None:
        label = self.query_one(Label)
        direction = "" if self._forward else " backward"
        state = "Failing " if self._failed else ("Wrapped " if self._wrapped else "")
        label.update(f"{state}I-search{direction}: {self._query}")
        label.set_class(self._failed, "failing")
        # Highlight every occurrence in view while the search is live.
        self._editor.set_search_highlight(self._query)

    def _goto(self, start_index: int, length: int) -> None:
        document = self._editor.document
        start = document.get_location_from_index(start_index)
        end = document.get_location_from_index(start_index + length)
        self._match = (start, end)
        # Point at the far edge of the match in the search direction.
        if self._forward:
            self._editor.selection = Selection(start, end)
        else:
            self._editor.selection = Selection(end, start)
        self._editor.scroll_cursor_visible(center=True)

    # -- searching ---------------------------------------------------------------

    def _haystack_and_needle(self) -> tuple[str, str]:
        text = self._editor.text
        if self._query == self._query.lower():
            return text.lower(), self._query.lower()
        return text, self._query

    def _search(self, from_index: int) -> None:
        """Find the query starting at from_index (forward) or ending at or
        before it (backward), wrapping around."""
        text, query = self._haystack_and_needle()
        self._failed = self._wrapped = False
        if not query:
            self._match = None
            self._show()
            return
        if self._forward:
            found = text.find(query, from_index)
            if found == -1:
                found = text.find(query)
                self._wrapped = found != -1
        else:
            found = text.rfind(query, 0, from_index)
            if found == -1:
                found = text.rfind(query)
                self._wrapped = found != -1
        if found == -1:
            self._failed = True
        else:
            self._goto(found, len(query))
        self._show()

    def _index(self, location: tuple[int, int]) -> int:
        return self._editor.document.get_index_from_location(location)

    def _extend_search(self) -> None:
        """Query grew: re-anchor at the current match start (or the origin)."""
        if self._match is not None:
            anchor = self._index(self._match[0])
        else:
            anchor = self._index(self._origin.end)
        if self._forward:
            self._search(anchor)
        else:
            self._search(anchor + len(self._query))

    def _next_match(self, forward: bool) -> None:
        if not self._query:
            last = getattr(self.app, "last_search", "")
            if not last:
                return
            self._query = last
            self._forward = forward
            self._extend_search()
            return
        self._forward = forward
        if self._match is None:
            self._extend_search()
            return
        start, end = self._match
        if forward:
            self._search(self._index(start) + 1)
        else:
            self._search(self._index(end) - 1)

    # -- keys ---------------------------------------------------------------------

    def on_key(self, event: events.Key) -> None:
        event.stop()
        event.prevent_default()
        if event.key == "ctrl+s":
            self._next_match(True)
        elif event.key == "ctrl+r":
            self._next_match(False)
        elif event.key == "backspace":
            self._query = self._query[:-1]
            self._extend_search()
        elif event.key == "ctrl+g":
            self._editor.clear_search_highlight()
            self._editor.selection = self._origin
            self._editor.scroll_cursor_visible()
            self.dismiss()
        elif event.key in ("enter", "escape"):
            self._accept()
        elif event.is_printable and event.character:
            self._query += event.character
            self._extend_search()

    def _accept(self) -> None:
        if self._query:
            self.app.last_search = self._query
        # Lazy-highlight is only shown while searching; drop it on exit.
        self._editor.clear_search_highlight()
        # Leave point where the search ended; set the (inactive) mark at the
        # origin, as emacs does.
        cur = self._editor.point
        self._editor.selection = Selection(cur, cur)
        self._editor.mark = self._origin.end
        self._editor.mark_active = False
        self.dismiss()
