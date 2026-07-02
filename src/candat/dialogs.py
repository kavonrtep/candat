"""Minibuffer-style modal dialogs, docked to the bottom like emacs."""

from __future__ import annotations

import os
from pathlib import Path

from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.screen import ModalScreen
from textual.widgets import Input, Label


class PathInput(Input):
    """An Input where Tab completes filesystem paths."""

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "tab":
            event.stop()
            event.prevent_default()
            self._complete()
            return
        await super()._on_key(event)

    def _complete(self) -> None:
        value = os.path.expanduser(self.value)
        slash = value.rfind("/")
        base, prefix = value[: slash + 1], value[slash + 1 :]
        try:
            names = sorted(
                entry.name + ("/" if entry.is_dir() else "")
                for entry in Path(base or ".").iterdir()
                if entry.name.startswith(prefix)
                and (prefix.startswith(".") or not entry.name.startswith("."))
            )
        except OSError:
            names = []
        screen = self.screen
        hint = ""
        if not names:
            hint = "[no match]"
        else:
            common = os.path.commonprefix(names)
            if len(common) > len(prefix):
                self.value = base + common
                self.cursor_position = len(self.value)
            if len(names) > 1:
                hint = "{" + "  ".join(names[:8]) + ("  …}" if len(names) > 8 else "}")
        if isinstance(screen, PromptScreen):
            screen.show_hint(hint)


class PromptScreen(ModalScreen[str | None]):
    """A one-line prompt at the bottom of the screen; returns the entered text
    or None if cancelled. With complete_paths=True, Tab completes paths."""

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
    PromptScreen #hint {
        color: $foreground 50%;
        text-style: none;
        max-width: 50%;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+g", "cancel", "Cancel"),
    ]

    def __init__(
        self, prompt: str, initial: str = "", complete_paths: bool = False
    ) -> None:
        super().__init__()
        self._prompt = prompt
        self._initial = initial
        self._complete_paths = complete_paths

    def compose(self) -> ComposeResult:
        input_class = PathInput if self._complete_paths else Input
        with Horizontal():
            yield Label(self._prompt)
            yield input_class(value=self._initial)
            yield Label("", id="hint")

    def show_hint(self, hint: str) -> None:
        self.query_one("#hint", Label).update(hint)

    def on_mount(self) -> None:
        input_widget = self.query_one(Input)
        input_widget.focus()
        input_widget.cursor_position = len(self._initial)

    @on(Input.Changed)
    def _changed(self, event: Input.Changed) -> None:
        self.show_hint("")

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
