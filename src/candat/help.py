"""The help screen: a scrollable keybinding reference (F1 / C-x ?)."""

from __future__ import annotations

from textual import events
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Markdown

KEY_HELP = """\
# candat keys

`ESC` is the Meta prefix: `ESC w` == `M-w`, `ESC x` == `M-x`, and so on.

## Files & buffers

| Key | Action |
| --- | --- |
| `C-x C-f` | find file (creates it if missing; `Tab` completes paths) |
| `C-x C-s` | save buffer |
| `C-x C-w` | write buffer to another file |
| `C-x k` | kill (close) buffer |
| `C-x b` | buffer list (next buffer preselected) |
| `C-x C-c` | quit (asks about unsaved buffers) |

## Movement

| Key | Action |
| --- | --- |
| `C-f` / `C-b` | forward / backward char |
| `C-n` / `C-p` | next / previous line |
| `C-a` / `C-e` | beginning / end of line |
| `M-f` / `M-b` | forward / backward word |
| `C-v` / `M-v` | page down / page up |
| `M-<` / `M->` | beginning / end of buffer |

## Mark & region

| Key | Action |
| --- | --- |
| `C-space` | set mark; movement then extends the region |
| `C-x C-x` | exchange point and mark |
| `C-x h` | mark whole buffer |
| `C-g` | deactivate mark |

## Kill ring & editing

| Key | Action |
| --- | --- |
| `C-k` | kill line (consecutive kills join) |
| `C-w` / `M-w` | kill / copy region |
| `C-y` / `M-y` | yank / yank-pop |
| `M-d` / `M-backspace` | kill word forward / backward |
| `M-up` / `M-down` | move line or marked block up / down |
| `M-;` | toggle line comment on line or region |
| `C-x u`, `C-/`, `C-z` | undo |
| `C-d` | delete char |

## Search & replace

| Key | Action |
| --- | --- |
| `C-s` / `C-r` | incremental search forward / backward |
| `C-s C-s` | repeat last search |
| `M-%` | query-replace: `y` replace, `n` skip, `!` all, `q` stop |
| `C-x g` | search project tree (regex; Enter jumps to match) |
| `C-g` | cancel search, back to start |
| `Enter` | accept search position |

## Panels & tools

| Key | Action |
| --- | --- |
| `C-c C-c` | send region or current line to the terminal REPL |
| `C-x t` | toggle the terminal panel |
| `Shift+PgUp/PgDn` | terminal scrollback (typing snaps back) |
| `C-x o` | cycle focus: tree → editor → terminal |
| `C-c C-v` | markdown preview: split / preview-only / off |
| `M-x`, `Ctrl+Shift+P` | command palette |
| `F1`, `C-x ?` | this help |

In the terminal panel all keys go to the shell (including `C-c`);
only `C-x` is reserved, so `C-x t` and `C-x o` always work.

*Press `q`, `Esc`, or `C-g` to close this help.*
"""


class HelpScreen(ModalScreen[None]):
    CSS = """
    HelpScreen {
        align: center middle;
    }
    HelpScreen VerticalScroll {
        width: 80%;
        max-width: 100;
        height: 85%;
        background: $background;
        border: solid $primary;
        padding: 0 2;
    }
    """

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Markdown(KEY_HELP)

    def on_key(self, event: events.Key) -> None:
        if event.key in ("escape", "q", "ctrl+g", "f1", "enter"):
            event.stop()
            event.prevent_default()
            self.dismiss()
        elif event.key in ("up", "down", "pageup", "pagedown", "ctrl+n", "ctrl+p"):
            event.stop()
            scroll = self.query_one(VerticalScroll)
            if event.key in ("up", "ctrl+p"):
                scroll.scroll_up()
            elif event.key in ("down", "ctrl+n"):
                scroll.scroll_down()
            elif event.key == "pageup":
                scroll.scroll_page_up()
            else:
                scroll.scroll_page_down()
