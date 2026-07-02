"""The buffer list (C-x b): pick an open buffer from a small modal list."""

from __future__ import annotations

from rich.text import Text
from textual import events, on
from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.widgets import OptionList
from textual.widgets.option_list import Option


class BufferListScreen(ModalScreen[str | None]):
    """Shows open buffers; returns the chosen pane id (or None)."""

    CSS = """
    BufferListScreen {
        align: center middle;
    }
    BufferListScreen OptionList {
        width: 70%;
        max-width: 90;
        max-height: 60%;
        background: $background;
        border: solid $primary;
        padding: 0 1;
    }
    """

    def __init__(self, buffers: list[tuple[str, str]], preselect: int = 0) -> None:
        """buffers: (pane_id, label) pairs in tab order."""
        super().__init__()
        self._buffers = buffers
        self._preselect = preselect

    def compose(self) -> ComposeResult:
        yield OptionList(
            *[
                Option(Text.from_markup(label), id=pane_id)
                for pane_id, label in self._buffers
            ]
        )

    def on_mount(self) -> None:
        option_list = self.query_one(OptionList)
        option_list.highlighted = self._preselect
        option_list.focus()

    @on(OptionList.OptionSelected)
    def _selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def on_key(self, event: events.Key) -> None:
        if event.key in ("escape", "ctrl+g"):
            event.stop()
            self.dismiss(None)
        elif event.key in ("ctrl+n", "ctrl+p"):
            event.stop()
            option_list = self.query_one(OptionList)
            option_list.action_cursor_down() if event.key == "ctrl+n" else option_list.action_cursor_up()
