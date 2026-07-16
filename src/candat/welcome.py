"""The startup welcome screen: the candat fish and a few key hints.

Shown in place of the empty untitled buffer when candat starts with nothing
to open (no file arguments, no session to restore) — like emacs' splash or
vim's intro. It vanishes as soon as the buffer is used: typing a character
dismisses it and inserts that character into the (scratch) buffer, and
opening a file replaces it entirely.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from textual import events
from textual.widgets import Static

try:
    VERSION = version("candat")
except PackageNotFoundError:  # running from a source tree
    VERSION = "dev"

# A zander — "candát" — swimming through the terminal. Braille art
# generated from docs/candat-fish.png by docs/make_fish.py.
FISH = """\
                    ⣠⣀⣤⣀⡄⢀
                  ⣼⢹⠃⡜⢡⠏⡨⢛⡶⣦⡄       ⣀⣀⣀
                 ⢰⣇⣏⣼⣰⣋⣞⣡⢋⠔⡡⢛⢷⡀  ⢀⣴⢯⡫⣳⣝⡻⣒⢤⡀
         ⢀⣀⣠⠤⠶⠒⠛⢻⣿⣯⠁ ⠸⣿⣿⡏⠉⠉⢿⣷⣿⠛⠶⣶⣿⣵⣽⣿⣮⣳⣝⢮⡳⣮⡳⣄       ⢀⣠⣤⡶⠶⡻
   ⣀⣀⡤⢴⢶⣏⠉ ⢠ ⠑⠲⢄⠈⣿⡿   ⢿⣿⡇  ⢸⣿⣿  ⢸⣿⣿  ⠸⣿⣿⠙⠛⣾⣿⡯⣥⣀⣀⣠⣤⣴⣿⣿⣝⣒⢉⡜⠁
  ⣯⣟⡤⣄⡘⠿⠟   ⡇ ⢀⡼⢁⣙⣧⣤⡤⢤⢸⣿⠇   ⣿⣿  ⠈⣿⣿  ⠈⣿⡿  ⢸⣿⠃ ⢹⡿  ⠈⣿⡿⠶⢢⡎
  ⠑⠪⢭⣓⣼   ⣀⢜⣤⣔⡿⠃⣻⣿⣟⢛⡱⠃⠸⡟    ⢻⠇   ⠹⠃   ⠙⠁⣀⣀⣠⡥⠤⠤⠤⠥⠤⣤⣴⣿⣿⡭⢔⡱⡄
      ⠉⠉⠙⠓⠒⠻⠷⠭⠤⢤⣄⣉⣩⣉⣀⣀⣀⣀⣀⣀⣀⣀⣀⣀⣀⣤⡤⠤⠤⣶⣶⣾⢟⡽⣫⢟⣱⠆      ⠈⠛⠿⣯⣵⣒⠘⣆
                 ⠹⣿⣿⢟⢻⠉⠉⠉⠉         ⠈⠛⢵⣽⡫⣰⠟⠁           ⠉⠙⠛⠚
                  ⠈⠓⠵⡼                ⠈⠉⠁\
"""

HINTS: list[tuple[str, str]] = [
    ("C-x C-f", "open or create a file"),
    ("C-x b", "switch buffer"),
    ("C-x t", "toggle the terminal"),
    ("M-x", "command palette"),
    ("F1", "all keybindings"),
    ("C-x C-c", "quit"),
]


def welcome_markup() -> str:
    art = "\n".join(f"[bold $primary]{line}[/]" for line in FISH.splitlines())
    title = f"[bold]candat[/] [dim]{VERSION}[/]"
    width = max(len(key) for key, _ in HINTS)
    hints = "\n".join(
        f"[bold]{key:<{width}}[/]   [dim]{text}[/]" for key, text in HINTS
    )
    footer = "[dim]start typing to use this buffer as scratch[/]"
    return f"{art}\n\n{title}\n\n{hints}\n\n{footer}"


class Welcome(Static, can_focus=True):
    """The splash content; any printable key hands over to the editor."""

    def on_mount(self) -> None:
        self.update(welcome_markup())

    def on_key(self, event: events.Key) -> None:
        if not (event.is_printable or event.key in ("enter", "escape")):
            return  # chords, palette, F1 bubble up to the app as usual
        event.stop()
        event.prevent_default()
        # Duck-typed to avoid importing pane (which imports this module).
        pane = next(
            (a for a in self.ancestors_with_self if hasattr(a, "leave_welcome_mode")),
            None,
        )
        if pane is None:
            return
        pane.leave_welcome_mode()
        editor = pane.editor
        editor.focus()
        if event.is_printable and event.character:
            editor.insert(event.character)
