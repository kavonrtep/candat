"""Emacs-style prefix-key (chord) dispatcher.

Pressing a prefix (e.g. C-x) pushes a ChordScreen, which shows the pending
prefix in a minibuffer-style line, captures exactly one more key, and runs
the app action mapped to it. C-g or escape cancels, as in emacs.
"""

from __future__ import annotations

from textual import events
from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.widgets import Label

# Chord table for the C-x prefix: key -> (app action, description).
CTRL_X_MAP: dict[str, tuple[str, str]] = {
    "ctrl+f": ("find_file", "find file"),
    "ctrl+s": ("save_buffer", "save buffer"),
    "ctrl+w": ("write_file", "write file (save as)"),
    "ctrl+c": ("request_quit", "quit"),
    "ctrl+x": ("exchange_point_and_mark", "exchange point and mark"),
    "k": ("kill_buffer", "kill buffer"),
    "b": ("switch_buffer", "switch buffer"),
    "o": ("other_window", "other window"),
    "h": ("mark_whole_buffer", "mark whole buffer"),
    "u": ("undo_buffer", "undo"),
    "t": ("toggle_terminal", "toggle terminal"),
    "question_mark": ("help", "help"),
}

# Chord table for the C-c prefix (mode-specific commands, as in emacs).
CTRL_C_MAP: dict[str, tuple[str, str]] = {
    "ctrl+v": ("toggle_preview", "toggle markdown preview"),
}


class ChordScreen(ModalScreen[None]):
    """Captures the key that follows a prefix and dispatches the mapped action."""

    CSS = """
    ChordScreen {
        align: left bottom;
        background: transparent;
    }
    ChordScreen Label {
        height: 1;
        width: 100%;
        padding: 0 1;
        background: $panel;
        color: $foreground;
    }
    """

    def __init__(self, prefix: str, chord_map: dict[str, tuple[str, str]]) -> None:
        super().__init__()
        self._prefix = prefix
        self._chord_map = chord_map

    def compose(self) -> ComposeResult:
        yield Label(f"{self._prefix} -")

    def on_key(self, event: events.Key) -> None:
        event.stop()
        event.prevent_default()
        if event.key in ("escape", "ctrl+g"):
            self.dismiss()
            return
        entry = self._chord_map.get(event.key)
        self.dismiss()
        if entry is None:
            self.app.notify(
                f"{self._prefix} {pretty_key(event.key)} is undefined",
                severity="warning",
                timeout=2,
            )
        else:
            action, _ = entry
            # Deferred so the action runs after this screen has popped;
            # dismiss() cannot be awaited from the screen's own handler.
            self.app.call_later(self.app.run_action, action)


def pretty_key(key: str) -> str:
    """Render a Textual key name emacs-style: 'ctrl+f' -> 'C-f'."""
    return key.replace("ctrl+", "C-").replace("shift+", "S-")
