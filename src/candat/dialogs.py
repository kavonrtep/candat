"""Minibuffer-style modal dialogs, docked to the bottom like emacs."""

from __future__ import annotations

import os
from pathlib import Path

from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label, OptionList
from textual.widgets.option_list import Option


class CompletionList(OptionList):
    """The candidate list shown under a PathInput; Esc/Left returns to input."""

    def on_key(self, event: events.Key) -> None:
        if event.key in ("escape", "ctrl+g", "left"):
            event.stop()
            event.prevent_default()
            if isinstance(self.screen, PromptScreen):
                self.screen.hide_completions()
                self.screen.query_one(Input).focus()


class PathInput(Input):
    """An Input where Tab completes filesystem paths and shows the choices
    when more than one remains."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.programmatic = False  # set while we fill the value ourselves

    def _set_value(self, value: str) -> None:
        self.programmatic = True
        self.value = value
        self.cursor_position = len(value)
        self.call_after_refresh(setattr, self, "programmatic", False)

    async def _on_key(self, event: events.Key) -> None:
        screen = self.screen
        showing = isinstance(screen, PromptScreen) and screen.completions_visible
        if event.key == "tab":
            event.stop()
            event.prevent_default()
            if showing:
                screen.query_one(CompletionList).focus()  # step into the list
            else:
                self._complete()
            return
        if event.key in ("down", "ctrl+n") and showing:
            event.stop()
            event.prevent_default()
            screen.query_one(CompletionList).focus()
            return
        await super()._on_key(event)

    def _candidates(self) -> tuple[str, list[str]]:
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
        return base, names

    def _complete(self) -> None:
        if not isinstance(self.screen, PromptScreen):
            return
        base, names = self._candidates()
        prefix = os.path.expanduser(self.value)[len(base):]
        if not names:
            self.screen.hide_completions()
            self.screen.show_hint("[no match]")
            return
        common = os.path.commonprefix(names)
        if len(common) > len(prefix):
            self._set_value(base + common)
        if len(names) == 1:
            self.screen.hide_completions()
        else:
            self.screen.show_completions(base, names)


class PromptScreen(ModalScreen[str | None]):
    """A one-line prompt at the bottom of the screen; returns the entered text
    or None if cancelled. With complete_paths=True, Tab completes paths."""

    CSS = """
    PromptScreen {
        align: left bottom;
        background: transparent;
    }
    PromptScreen Vertical {
        height: auto;
        width: 100%;
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
    PromptScreen CompletionList {
        display: none;
        width: auto;
        max-width: 100%;
        max-height: 12;
        border: none;
        background: $panel;
        color: $foreground;
        scrollbar-size-vertical: 1;
    }
    PromptScreen.-completing CompletionList {
        display: block;
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
        self._base = ""

    @property
    def completions_visible(self) -> bool:
        return self.has_class("-completing")

    def compose(self) -> ComposeResult:
        input_class = PathInput if self._complete_paths else Input
        with Vertical():
            yield CompletionList()
            with Horizontal():
                yield Label(self._prompt)
                yield input_class(value=self._initial)
                yield Label("", id="hint")

    def show_hint(self, hint: str) -> None:
        self.query_one("#hint", Label).update(hint)

    def show_completions(self, base: str, names: list[str]) -> None:
        self._base = base
        self.show_hint("")
        options = self.query_one(CompletionList)
        options.clear_options()
        options.add_options([Option(name, id=name) for name in names])
        options.highlighted = 0
        self.add_class("-completing")

    def hide_completions(self) -> None:
        self.remove_class("-completing")

    def on_mount(self) -> None:
        input_widget = self.query_one(Input)
        input_widget.focus()
        input_widget.cursor_position = len(self._initial)

    @on(Input.Changed)
    def _changed(self, event: Input.Changed) -> None:
        # Real typing (not a completion fill) invalidates the shown choices.
        input_widget = self.query_one(Input)
        if getattr(input_widget, "programmatic", False):
            return
        if input_widget.has_focus:
            self.show_hint("")
            self.hide_completions()

    @on(OptionList.OptionSelected)
    def _completion_chosen(self, event: OptionList.OptionSelected) -> None:
        name = event.option.id or ""
        input_widget = self.query_one(Input)
        input_widget.value = self._base + name
        input_widget.cursor_position = len(input_widget.value)
        self.hide_completions()
        input_widget.focus()

    @on(Input.Submitted)
    def _submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def action_cancel(self) -> None:
        # Esc closes the completion list first, then cancels the prompt.
        if self.completions_visible and not self.query_one(Input).has_focus:
            self.hide_completions()
            self.query_one(Input).focus()
            return
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
