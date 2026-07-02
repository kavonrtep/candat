"""Minibuffer-style modal dialogs, docked to the bottom like emacs."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.screen import ModalScreen
from textual.widgets import Input, Label


class PromptScreen(ModalScreen[str | None]):
    """A one-line prompt at the bottom of the screen; returns the entered text
    or None if cancelled."""

    CSS = """
    PromptScreen {
        align: left bottom;
        background: transparent;
    }
    PromptScreen Horizontal {
        height: 1;
        width: 100%;
        background: $panel;
    }
    PromptScreen Label {
        padding: 0 1;
        color: $primary;
        text-style: bold;
    }
    PromptScreen Input {
        border: none;
        height: 1;
        width: 1fr;
        background: $panel;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+g", "cancel", "Cancel"),
    ]

    def __init__(self, prompt: str, initial: str = "") -> None:
        super().__init__()
        self._prompt = prompt
        self._initial = initial

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield Label(self._prompt)
            yield Input(value=self._initial)

    def on_mount(self) -> None:
        input_widget = self.query_one(Input)
        input_widget.focus()
        input_widget.cursor_position = len(self._initial)

    @on(Input.Submitted)
    def _submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ConfirmScreen(ModalScreen[bool]):
    """A yes/no question at the bottom of the screen."""

    CSS = """
    ConfirmScreen {
        align: left bottom;
        background: transparent;
    }
    ConfirmScreen Label {
        height: 1;
        width: 100%;
        padding: 0 1;
        background: $panel;
        color: $warning;
        text-style: bold;
    }
    """

    BINDINGS = [
        Binding("y", "answer(True)", "Yes"),
        Binding("n", "answer(False)", "No"),
        Binding("escape", "answer(False)", "No"),
        Binding("ctrl+g", "answer(False)", "No"),
    ]

    def __init__(self, question: str) -> None:
        super().__init__()
        self._question = question

    def compose(self) -> ComposeResult:
        yield Label(f"{self._question} (y/n)")

    def action_answer(self, answer: bool) -> None:
        self.dismiss(answer)
