"""M-x: pike commands for the Textual command palette."""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

from textual.command import DiscoveryHit, Hit, Hits, Provider

if TYPE_CHECKING:
    from .app import PikeApp

# (emacs command name, help text, app action)
COMMANDS: list[tuple[str, str, str]] = [
    ("find-file", "Open or create a file (C-x C-f)", "find_file"),
    ("save-buffer", "Save the current buffer (C-x C-s)", "save_buffer"),
    ("write-file", "Save the buffer under a new name (C-x C-w)", "write_file"),
    ("kill-buffer", "Close the current buffer (C-x k)", "kill_buffer"),
    ("switch-to-buffer", "Cycle to the next buffer (C-x b)", "switch_buffer"),
    ("other-window", "Move focus between tree and editor (C-x o)", "other_window"),
    ("mark-whole-buffer", "Select the entire buffer (C-x h)", "mark_whole_buffer"),
    ("exchange-point-and-mark", "Swap cursor and mark (C-x C-x)", "exchange_point_and_mark"),
    ("undo", "Undo the last edit (C-x u, C-/)", "undo_buffer"),
    ("markdown-toggle-preview", "Cycle markdown preview: split / preview-only / off (C-c C-v)", "toggle_preview"),
    ("isearch-forward", "Incremental search forward (C-s)", "isearch_forward"),
    ("isearch-backward", "Incremental search backward (C-r)", "isearch_backward"),
    ("help", "Show all keybindings (F1, C-x ?)", "help"),
    ("shell", "Toggle the terminal panel (C-x t)", "toggle_terminal"),
    ("save-buffers-kill-terminal", "Quit pike (C-x C-c)", "request_quit"),
]


class PikeCommands(Provider):
    async def discover(self) -> Hits:
        app: PikeApp = self.app  # type: ignore[assignment]
        for name, help_text, action in COMMANDS:
            yield DiscoveryHit(
                name,
                partial(app.run_action, action),
                help=help_text,
            )

    async def search(self, query: str) -> Hits:
        app: PikeApp = self.app  # type: ignore[assignment]
        matcher = self.matcher(query)
        for name, help_text, action in COMMANDS:
            score = matcher.match(name)
            if score > 0:
                yield Hit(
                    score,
                    matcher.highlight(name),
                    partial(app.run_action, action),
                    help=help_text,
                )
