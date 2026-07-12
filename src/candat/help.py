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
| `C-x C-f` | find file (`Tab` completes paths; lists choices if several) |
| `C-x C-s` | save buffer |
| `C-x C-w` | write buffer to another file |
| `C-x C-q` | toggle read-only for this buffer |
| `C-x C-r` | open a file read-only |
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
| `C-x w` | toggle soft wrap |
| `C-d` | delete char |

## Search & replace

| Key | Action |
| --- | --- |
| `C-s` / `C-r` | incremental search forward / backward (all visible matches highlighted) |
| `C-s C-s` | repeat last search |
| `M-%` | query-replace: `y` replace, `n` skip, `!` all, `q` stop |
| `M-g` | go to line number |
| `C-x g` | search project tree (regex; Enter jumps to match) |
| `C-g` | cancel search, back to start |
| `Enter` | accept search position |

## Table view (any delimited buffer)

`.csv` / `.tsv` files open as a table automatically (extend with
`table_suffixes` in the config). Any other buffer — a `.tab`, `.gff`, a log,
even an unsaved one — switches to a table with `C-c C-v`; the delimiter is
auto-detected and `d` re-picks it if the guess was wrong. This works from
the large-file pager too: both views stream, so `C-c C-v` flips a huge
delimited file between pager and table without ever loading it whole.

| Key | Action |
| --- | --- |
| `/` | search rows (literal, smart case — same as everywhere); matches are highlighted in the cells |
| `C-s` / `n` | next match |
| `C-r` / `N` | previous match |
| `C-g` / `Esc` | cancel search (clear the highlight) |
| `d` | change the delimiter (`,` `;` `pipe` `tab` `space` or any character) |
| `&` | filter rows by regex (empty clears) |
| `g` / `G` | first row / load all and go to last |
| `C-c C-v` | switch between table and raw text |

The table is read-only. Small files use the classic table; big ones (or
anything over ~200k rows) switch to a windowed table with **no row limit** —
only the visible rows are read, so millions of rows scroll instantly, `G`
jumps straight to the end, and search scans the whole file in the background
(`n`/`N` then step through every match, with a match counter). A buffer with
unsaved edits is parsed from the buffer text, so the table always matches
what you see.

## Windows (splits)

| Key | Action |
| --- | --- |
| `C-x 3` | split window side by side |
| `C-x 2` | split window stacked |
| `C-x o` | move to the next window |
| `C-x 0` | close the current window |
| `C-x 1` | close the other windows |

C-x 2 / C-x 3 open the current buffer in the new window as a linked view:
same file and edits, but its own cursor and scroll — inspect or edit two
places at once. C-x C-f / C-x b in a window can point it at another file.

## Large files (pager)

Text files over 10 MB open in a read-only `less`-style pager (not loaded into
the editor). Binary files show a placeholder. Extremely long single lines are
shown up to their first 64 KB (marked with `…`).

| Key | Action |
| --- | --- |
| arrows, `C-n` / `C-p` | line down / up |
| PgDn / PgUp, `C-v` / `M-v` | page down / up |
| `g` / `G` | first / last line |
| ← / →, `C-b` / `C-f` | scroll left / right (when not wrapped) |
| `C-x w` | toggle wrap (off by default; truncated lines show `›`) |
| `/` / `?` | new search forward / backward (smart case) |
| `C-s` / `C-r`, `n` / `N` | next / previous match (all matches in view are highlighted) |
| `C-g` / `Esc` | cancel search (clear the highlight) — or stop a running one |
| `M-g` | go to line |
| `F` | follow a growing file, like `less +F` (any key stops) |
| `e` / `v` | load the whole file into the editor anyway (asks first) |

## Panels & tools

| Key | Action |
| --- | --- |
| `C-c C-c` | send region or current line to the terminal REPL |
| `C-x t` | toggle the terminal panel |
| `Shift+PgUp/PgDn` | terminal scrollback (typing snaps back) |
| `C-x o` | cycle focus: tree → editor → terminal |
| `/` (in file tree) | filter the tree by path; `Esc` clears |
| `C-c C-v` | alternate view: markdown preview cycle, or table view of any other buffer |
| `M-x`, `Ctrl+Shift+P` | command palette |
| `F1`, `C-x ?` | this help |

In the terminal panel all keys go to the shell (including `C-c`);
only `C-x` is reserved, so `C-x t` and `C-x o` always work.

Open files are watched: a buffer without local edits reloads when its
file changes on disk; with local edits you are asked first (pager and
CSV views track the file in place). The status bar shows `--` clean,
`**` modified, `%%` read-only.

## Configuration

`~/.config/candat/config.toml` (XDG aware): `tree_icons` ("emoji" /
"nerd" / "ascii"; `cycle-tree-icons` saves your choice), `pager_wrap`
(start the pager wrapped), `tabstop` (tab width in the pager), and
`restore_session` (start `candat` with no files → the previous
session's files reopen, with cursors and the active tab). The
`CANDAT_TREE_ICONS` environment variable overrides the config.

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
