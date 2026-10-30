"""Query-replace (M-%): interactive find/replace, emacs-style.

Walks matches from point to the end of the buffer; y/space replaces,
n skips, ! replaces the rest without asking, q/Enter/C-g stops. Smart
case like isearch: an all-lowercase pattern matches case-insensitively.
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


class QueryReplaceScreen(ModalScreen[None]):
    CSS = """
    QueryReplaceScreen {
        align: left bottom;
        background: transparent;
    }
    QueryReplaceScreen Label {
        height: 1;
        width: 100%;
        padding: 0 1;
        background: $panel;
        color: $foreground;
    }
    """

    def __init__(self, editor: EditorBuffer, find: str, replacement: str) -> None:
        super().__init__()
        self._editor = editor
        self._find = find
        self._replacement = replacement
        self._fold = find == find.lower()  # smart case
        self._match: tuple[tuple[int, int], tuple[int, int]] | None = None
        self._count = 0

    def compose(self) -> ComposeResult:
        yield Label(
            f"Query replacing {self._find} with {self._replacement}: (y/n/!/q)"
        )

    def on_mount(self) -> None:
        start = self._editor.document.get_index_from_location(self._editor.point)
        self._find_next(start)

    def _haystack(self) -> str:
        text = self._editor.text
        return text.lower() if self._fold else text

    def _find_next(self, from_index: int) -> None:
        found = self._haystack().find(
            self._find.lower() if self._fold else self._find, from_index
        )
        if found == -1:
            self._finish()
            return
        document = self._editor.document
        start = document.get_location_from_index(found)
        end = document.get_location_from_index(found + len(self._find))
        self._match = (start, end)
        self._editor.selection = Selection(start, end)
        self._editor.scroll_cursor_visible(center=True)

    def _replace_current(self) -> int:
        """Replace the current match; returns the index just past the
        inserted replacement."""
        assert self._match is not None
        start, end = self._match
        self._editor.replace(self._replacement, start, end)
        self._count += 1
        document = self._editor.document
        return document.get_index_from_location(start) + len(self._replacement)

    def on_key(self, event: events.Key) -> None:
        event.stop()
        event.prevent_default()
        if self._match is None:
            self._finish()
            return
        key = event.key
        if key in ("y", "space"):
            self._find_next(self._replace_current())
        elif key in ("n", "backspace", "delete"):
            document = self._editor.document
            start, _ = self._match
            self._find_next(document.get_index_from_location(start) + 1)
        elif key == "exclamation_mark":
            self._replace_rest()
            self._finish()
        elif key in ("q", "enter", "escape", "ctrl+g"):
            self._finish()

    def _replace_rest(self) -> None:
        """`!`: replace the current match and every later one in a single
        edit — one pass over one text snapshot and one undo step, instead of
        an edit (and a whole-document rescan) per match."""
        assert self._match is not None
        document = self._editor.document
        text = self._editor.text
        haystack = text.lower() if self._fold else text
        needle = self._find.lower() if self._fold else self._find
        first = document.get_index_from_location(self._match[0])
        pieces: list[str] = []
        pos = first
        found = first
        while found != -1:
            pieces.append(text[pos:found])
            pieces.append(self._replacement)
            pos = found + len(needle)
            self._count += 1
            found = haystack.find(needle, pos)
        start = self._match[0]
        end = document.get_location_from_index(pos)
        self._editor.replace("".join(pieces), start, end)
        self._match = None
        # Point ends after the last replacement, as with y-by-y replacing.
        self._editor.move_cursor(
            document.get_location_from_index(
                first + sum(len(piece) for piece in pieces)
            )
        )

    def _finish(self) -> None:
        cur = self._editor.point
        self._editor.selection = Selection(cur, cur)
        self.dismiss()
        plural = "" if self._count == 1 else "s"
        self.app.notify(f"Replaced {self._count} occurrence{plural}", timeout=2)
