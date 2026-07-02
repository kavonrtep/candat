"""A full PTY terminal panel: forkpty + pyte, rendered dark-on-light.

While the terminal has focus, keys pass through raw to the shell — including
C-c and C-g. Only C-x is reserved (the app's chord prefix), so C-x o / C-x t
always work as the way out.
"""

from __future__ import annotations

import fcntl
import os
import pty
import signal
import struct
import termios
import time

import pyte
from rich.style import Style
from rich.text import Text
from textual import events
from textual.widget import Widget

# ANSI palette tuned for a white background (dark-on-light, Konsole
# BlackOnWhite-flavored, high contrast).
ANSI_COLORS = {
    "default": None,  # falls back to the widget's own fg/bg
    "black": "#000000",
    "red": "#c01c28",
    "green": "#116329",
    "brown": "#986801",  # pyte's name for ANSI yellow
    "blue": "#0550ae",
    "magenta": "#8250df",
    "cyan": "#0e7490",
    "white": "#d0d0d0",
    "brightblack": "#5c5c5c",
    "brightred": "#e01b24",
    "brightgreen": "#188038",
    "brightbrown": "#b07d02",
    "brightyellow": "#b07d02",
    "brightblue": "#1a6fdb",
    "brightmagenta": "#9a5cf0",
    "brightcyan": "#0f8fb0",
    "brightwhite": "#ffffff",
}

# Textual key name -> bytes written to the pty.
KEY_BYTES = {
    "enter": b"\r",
    "backspace": b"\x7f",
    "tab": b"\t",
    "escape": b"\x1b",
    "up": b"\x1b[A",
    "down": b"\x1b[B",
    "right": b"\x1b[C",
    "left": b"\x1b[D",
    "home": b"\x1b[H",
    "end": b"\x1b[F",
    "pageup": b"\x1b[5~",
    "pagedown": b"\x1b[6~",
    "delete": b"\x1b[3~",
    "insert": b"\x1b[2~",
    "f1": b"\x1bOP",
    "f2": b"\x1bOQ",
    "f3": b"\x1bOR",
    "f4": b"\x1bOS",
    "f5": b"\x1b[15~",
    "f6": b"\x1b[17~",
    "f7": b"\x1b[18~",
    "f8": b"\x1b[19~",
    "f9": b"\x1b[20~",
    "f10": b"\x1b[21~",
    "f11": b"\x1b[23~",
    "f12": b"\x1b[24~",
    "shift+tab": b"\x1b[Z",
}


def _key_to_bytes(event: events.Key) -> bytes | None:
    key = event.key
    if key in KEY_BYTES:
        return KEY_BYTES[key]
    if event.is_printable and event.character:
        return event.character.encode()
    if key.startswith("ctrl+") and len(key) == 6:
        char = key[5]
        if "a" <= char <= "z":
            return bytes([ord(char) - 96])
    # C-space / C-@ sends NUL; ctrl+underscore is C-/.
    if key in ("ctrl+@", "ctrl+at"):
        return b"\x00"
    if key == "ctrl+underscore":
        return b"\x1f"
    if key.startswith("alt+"):
        rest = key[4:]
        if len(rest) == 1:
            return b"\x1b" + rest.encode()
        if rest in KEY_BYTES:
            return b"\x1b" + KEY_BYTES[rest]
    return None


class TerminalPane(Widget):
    """An interactive shell running in a pty, emulated with pyte."""

    can_focus = True

    DEFAULT_CSS = """
    TerminalPane {
        height: 12;
        background: #ffffff;
        color: #000000;
        border-top: solid $panel;
        padding: 0 1;
        display: none;
    }
    TerminalPane.-open {
        display: block;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._pid: int | None = None
        self._fd: int | None = None
        self._screen: pyte.Screen | None = None
        self._stream: pyte.ByteStream | None = None
        self._exited = False

    @property
    def running(self) -> bool:
        return self._pid is not None and not self._exited

    # -- process lifecycle -----------------------------------------------------

    def spawn(self) -> None:
        if self.running:
            return
        cols = max(self.content_size.width, 20)
        rows = max(self.content_size.height, 4)
        self._screen = pyte.Screen(cols, rows)
        self._stream = pyte.ByteStream(self._screen)
        self._exited = False
        pid, fd = pty.fork()
        if pid == 0:  # child: become the shell
            shell = os.environ.get("SHELL", "/bin/bash")
            env = dict(os.environ, TERM="xterm-256color", COLORTERM="truecolor")
            try:
                os.execvpe(shell, [shell], env)
            finally:
                os._exit(1)
        self._pid, self._fd = pid, fd
        self._set_winsize(rows, cols)
        self.run_worker(self._reader, thread=True, exclusive=False)

    def _set_winsize(self, rows: int, cols: int) -> None:
        if self._fd is None:
            return
        fcntl.ioctl(
            self._fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0)
        )
        if self._pid:
            try:
                os.kill(self._pid, signal.SIGWINCH)
            except ProcessLookupError:
                pass

    def _reader(self) -> None:
        """Worker thread: pump pty output into the pyte screen."""
        fd = self._fd
        while fd is not None:
            try:
                data = os.read(fd, 65536)
            except OSError:
                break
            if not data:
                break
            self.app.call_from_thread(self._feed, data)
        self.app.call_from_thread(self._on_exit)

    def _feed(self, data: bytes) -> None:
        if self._stream is not None:
            self._stream.feed(data)
            self.refresh()

    def _on_exit(self) -> None:
        self._exited = True
        self._reap()
        self.refresh()

    def _reap(self) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
        if self._pid is not None:
            try:
                os.waitpid(self._pid, os.WNOHANG)
            except ChildProcessError:
                pass
            self._pid = None

    def kill(self) -> None:
        pid = self._pid
        if pid is not None:
            try:
                os.kill(pid, signal.SIGHUP)
            except ProcessLookupError:
                pid = None
        self._reap()
        if pid is not None:
            # Reap for real: brief grace for SIGHUP, then SIGKILL.
            for _ in range(50):
                try:
                    done, _ = os.waitpid(pid, os.WNOHANG)
                except ChildProcessError:
                    break
                if done:
                    break
                time.sleep(0.01)
            else:
                try:
                    os.kill(pid, signal.SIGKILL)
                    os.waitpid(pid, 0)
                except (ProcessLookupError, ChildProcessError):
                    pass
        self._exited = True

    def on_unmount(self) -> None:
        self.kill()

    def on_resize(self, event: events.Resize) -> None:
        if self._screen is not None and self.running:
            cols = max(self.content_size.width, 20)
            rows = max(self.content_size.height, 4)
            self._screen.resize(rows, cols)
            self._set_winsize(rows, cols)

    # -- input -----------------------------------------------------------------

    def on_key(self, event: events.Key) -> None:
        if not self.running or self._fd is None:
            return
        data = _key_to_bytes(event)
        if data is not None:
            event.stop()
            event.prevent_default()
            os.write(self._fd, data)

    def on_paste(self, event: events.Paste) -> None:
        if self.running and self._fd is not None:
            event.stop()
            os.write(self._fd, event.text.encode())

    # -- rendering ---------------------------------------------------------------

    def _char_style(self, char) -> Style:
        fg = ANSI_COLORS.get(char.fg)
        if fg is None and char.fg != "default":
            fg = f"#{char.fg}" if not char.fg.startswith("#") else char.fg
        bg = ANSI_COLORS.get(char.bg)
        if bg is None and char.bg != "default":
            bg = f"#{char.bg}" if not char.bg.startswith("#") else char.bg
        return Style(
            color=fg,
            bgcolor=bg,
            bold=char.bold,
            italic=char.italics,
            underline=char.underscore,
            strike=char.strikethrough,
            reverse=char.reverse,
        )

    def render(self) -> Text:
        if self._screen is None:
            return Text("terminal not started")
        text = Text(no_wrap=True, end="")
        screen = self._screen
        cursor = screen.cursor
        show_cursor = self.has_focus and not cursor.hidden and self.running
        for row in range(screen.lines):
            if row:
                text.append("\n")
            line = screen.buffer[row]
            for col in range(screen.columns):
                char = line[col]
                style = self._char_style(char)
                if show_cursor and row == cursor.y and col == cursor.x:
                    style += Style(reverse=True)
                text.append(char.data or " ", style)
        if self._exited:
            text.append("\n[process exited — C-x t to close, reopen to restart]")
        return text
