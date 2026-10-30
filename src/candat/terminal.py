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
import threading
import time
from collections import deque

import pyte
from rich.style import Style
from rich.text import Text
from textual import events
from textual.widget import Widget

from . import config

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


class InputPump:
    """Writes input to the pty on its own thread, in order.

    A pty's input buffer is small; when the program behind it stops
    reading (a busy REPL), a plain os.write blocks — on the UI thread that
    froze the whole app. Here the UI only queues bytes; `pending` counts
    what is not yet written, and `discard` drops the queue (C-c).
    """

    CHUNK = 4096

    def __init__(self, fd: int) -> None:
        self._fd = fd
        self._chunks: deque[bytes] = deque()
        self._cond = threading.Condition()
        self._closed = False
        self.pending = 0
        threading.Thread(target=self._run, daemon=True, name="pty-input").start()

    def write(self, data: bytes) -> None:
        with self._cond:
            if self._closed:
                return
            # Small pieces so a discard can drop most of a big paste.
            for i in range(0, len(data), self.CHUNK):
                self._chunks.append(data[i : i + self.CHUNK])
            self.pending += len(data)
            self._cond.notify()

    def discard(self) -> int:
        """Drop queued input (the piece being written still finishes);
        returns the number of bytes dropped."""
        with self._cond:
            dropped = sum(len(chunk) for chunk in self._chunks)
            self._chunks.clear()
            self.pending -= dropped
            return dropped

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._chunks.clear()
            self.pending = 0
            self._cond.notify()

    def _run(self) -> None:
        while True:
            with self._cond:
                while not self._chunks and not self._closed:
                    self._cond.wait()
                if self._closed:
                    return
                data = self._chunks.popleft()
            while data:
                try:
                    written = os.write(self._fd, data)
                except OSError:  # the shell went away
                    self.close()
                    return
                data = data[written:]
                with self._cond:
                    if self._closed:
                        return
                    self.pending -= written


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
        self._screen: pyte.HistoryScreen | None = None
        self._stream: pyte.ByteStream | None = None
        self._exited = False
        self._line_cache: dict[int, Text] = {}
        # Input sent before the shell's first output is queued: a starting
        # shell's terminal setup (tcsetattr) can discard early input.
        self._seen_output = False
        self._pending_input: list[bytes] = []
        self._pump: InputPump | None = None
        self._pump_timer = None
        # Spawn is deferred until the widget has a real size — forking while
        # hidden would size the pty 20x4 and early output would wrap and
        # scroll off that tiny screen before the resize catches up.
        self._spawn_pending = False

    @property
    def running(self) -> bool:
        return self._pid is not None and not self._exited

    @property
    def scrolled_back(self) -> bool:
        screen = self._screen
        return screen is not None and screen.history.position < screen.history.size

    # -- process lifecycle -----------------------------------------------------

    def spawn(self) -> None:
        if self.running:
            return
        if self.content_size.width < 2 or self.content_size.height < 2:
            # Not laid out yet (just un-hidden): fork on the next resize.
            self._spawn_pending = True
            return
        self._spawn_pending = False
        cols = self.content_size.width
        rows = self.content_size.height
        history = config.load()["terminal_history"]
        if not isinstance(history, int) or history < 100:
            history = 2000
        self._screen = pyte.HistoryScreen(cols, rows, history=history)
        self._stream = pyte.ByteStream(self._screen)
        self._exited = False
        self._line_cache.clear()
        self._seen_output = False
        # _pending_input is kept: send_text may have queued input already.
        pid, fd = pty.fork()
        if pid == 0:  # child: become the shell
            shell = os.environ.get("SHELL", "/bin/bash")
            env = dict(os.environ, TERM="xterm-256color", COLORTERM="truecolor")
            try:
                os.execvpe(shell, [shell], env)
            finally:
                os._exit(1)
        self._pid, self._fd = pid, fd
        self._pump = InputPump(fd)
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
        if self._stream is None or self._screen is None:
            return
        if not self._seen_output:
            # First output means the shell finished its terminal setup and
            # is reading input; flush anything sent before that.
            self._seen_output = True
            if self._pending_input:
                for chunk in self._pending_input:
                    self._write(chunk)
                self._pending_input.clear()
        old_cursor_row = self._screen.cursor.y
        self._stream.feed(data)
        # Only re-render rows pyte marked dirty (plus both cursor rows).
        for row in self._screen.dirty | {old_cursor_row, self._screen.cursor.y}:
            self._line_cache.pop(row, None)
        self._screen.dirty.clear()
        self.refresh()

    def _on_exit(self) -> None:
        self._exited = True
        self._reap()
        self._pending_input.clear()  # don't leak into a future session
        self.refresh()

    def _reap(self) -> None:
        if self._pump is not None:
            self._pump.close()
            self._pump = None
            self._update_title()
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
        self._spawn_pending = False
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
        if self._spawn_pending and not self.running:
            self.spawn()
            return
        if self._screen is not None and self.running:
            cols = max(self.content_size.width, 20)
            rows = max(self.content_size.height, 4)
            self._screen.resize(rows, cols)
            self._set_winsize(rows, cols)
            self._line_cache.clear()

    # -- input -----------------------------------------------------------------

    def on_key(self, event: events.Key) -> None:
        if not self.running or self._fd is None or self._screen is None:
            return
        # Scrollback: Shift+PageUp/PageDown page through history.
        if event.key in ("shift+pageup", "shift+pagedown"):
            event.stop()
            event.prevent_default()
            if event.key == "shift+pageup":
                self._screen.prev_page()
            else:
                self._screen.next_page()
            self._update_scrollback_state()
            return
        data = _key_to_bytes(event)
        if data is not None:
            event.stop()
            event.prevent_default()
            if self.scrolled_back:
                # Typing snaps back to the live view, like most terminals.
                while self.scrolled_back:
                    self._screen.next_page()
                self._update_scrollback_state()
            if event.key == "ctrl+c" and self._interrupt_queued():
                return
            self._write(data)

    # -- queued input ----------------------------------------------------------

    def _write(self, data: bytes) -> None:
        if self._pump is None:
            return
        self._pump.write(data)
        if self._pump.pending and self._pump_timer is None:
            self._pump_timer = self.set_interval(0.25, self._update_title)

    @property
    def queued_bytes(self) -> int:
        return self._pump.pending if self._pump is not None else 0

    def _interrupt_queued(self) -> bool:
        """C-c while input is still queued: drop the queue and interrupt the
        foreground job directly (a ^C byte would wait behind the queue)."""
        if self._pump is None or not self._pump.pending:
            return False
        self._pump.discard()
        try:
            os.killpg(os.tcgetpgrp(self._fd), signal.SIGINT)
        except (OSError, TypeError):
            pass
        # The piece in flight may end mid-line; a ^C behind it makes the
        # line discipline flush unread input and the shell drop the line.
        self._pump.write(b"\x03")
        self._update_title()
        return True

    def _update_title(self) -> None:
        queued = self.queued_bytes
        if self.scrolled_back:
            title = "history — Shift+PgDn to return"
        elif queued:
            kb = max(1, queued // 1024)
            title = f"sending input… {kb} KB queued — C-c discards"
        else:
            title = None
        self.border_title = title
        if not queued and self._pump_timer is not None:
            self._pump_timer.stop()
            self._pump_timer = None

    def _update_scrollback_state(self) -> None:
        self._line_cache.clear()
        self._update_title()
        self.refresh()

    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        # Wheel scrolls the history, like a real terminal emulator.
        if self._screen is None:
            return
        event.stop()
        self._screen.prev_page()
        self._update_scrollback_state()

    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        if self._screen is None or not self.scrolled_back:
            return
        event.stop()
        self._screen.next_page()
        self._update_scrollback_state()

    def on_paste(self, event: events.Paste) -> None:
        if self.running and self._fd is not None:
            event.stop()
            self._write(event.text.encode())

    def send_text(self, text: str) -> None:
        """Write text to the shell's stdin (used by send-to-REPL). Until the
        shell exists and has produced its first output, text is queued — a
        starting shell's terminal setup can discard early input, and the
        spawn itself may still be waiting for layout."""
        if self.running and self._seen_output and self._fd is not None:
            self._write(text.encode())
        else:
            self._pending_input.append(text.encode())

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

    def _render_row(self, row: int, cursor_col: int | None) -> Text:
        screen = self._screen
        assert screen is not None
        text = Text(no_wrap=True, end="")
        line = screen.buffer[row]
        for col in range(screen.columns):
            char = line[col]
            style = self._char_style(char)
            if col == cursor_col:
                style += Style(reverse=True)
            text.append(char.data or " ", style)
        return text

    def render(self) -> Text:
        if self._screen is None:
            return Text("terminal not started")
        screen = self._screen
        cursor = screen.cursor
        show_cursor = (
            self.has_focus
            and not cursor.hidden
            and self.running
            and not self.scrolled_back
        )
        text = Text(no_wrap=True, end="")
        for row in range(screen.lines):
            if row:
                text.append("\n")
            cursor_col = cursor.x if (show_cursor and row == cursor.y) else None
            if cursor_col is None and row in self._line_cache:
                line = self._line_cache[row]
            else:
                line = self._render_row(row, cursor_col)
                if cursor_col is None:
                    self._line_cache[row] = line
            text.append_text(line)
        if self._exited:
            text.append("\n[process exited — C-x t to close, reopen to restart]")
        return text
