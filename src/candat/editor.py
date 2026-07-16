"""Editor buffer widget: a TextArea with emacs editing, bound to an optional file.

Emacs layer: mark/region (C-space, movement extends the region while the mark
is active), the kill ring (C-k, C-w, M-w, C-y, M-y, M-d, M-backspace), and
emacs movement keys. The kill ring itself lives on the app so it is shared
between buffers.
"""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path

from rich.style import Style
from textual import events
from textual.binding import Binding
from textual.widgets import TextArea
from textual.widgets.text_area import Selection

from . import clipboard, config, markdown

LANGUAGES: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".md": "markdown",
    ".markdown": "markdown",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
    ".html": "html",
    ".htm": "html",
    ".xml": "xml",
    ".css": "css",
    ".tcss": "css",
    ".toml": "toml",
    ".js": "javascript",
    ".sql": "sql",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".r": "r",
    ".rmd": "markdown",
    # config formats
    ".ini": "ini",
    ".cfg": "ini",
    ".conf": "ini",
    ".service": "ini",
    ".mk": "make",
    ".mak": "make",
    ".dockerfile": "dockerfile",
    ".env": "bash",
}

# Files matched by exact name (lowercased) rather than extension.
FILENAMES: dict[str, str] = {
    "makefile": "make",
    "gnumakefile": "make",
    "dockerfile": "dockerfile",
    "containerfile": "dockerfile",
    "setup.cfg": "ini",
    "tox.ini": "ini",
    "pytest.ini": "ini",
    "mypy.ini": "ini",
    ".editorconfig": "ini",
    ".gitconfig": "ini",
    ".flake8": "ini",
    ".pylintrc": "ini",
    ".coveragerc": "ini",
    ".bashrc": "bash",
    ".bash_profile": "bash",
    ".bash_aliases": "bash",
    ".profile": "bash",
    ".zshrc": "bash",
    ".zshenv": "bash",
    ".zprofile": "bash",
    ".inputrc": "bash",
}


def language_for(path: Path | None) -> str | None:
    if path is None:
        return None
    name = path.name.lower()
    if name in FILENAMES:
        return FILENAMES[name]
    if name.startswith("dockerfile"):  # Dockerfile.dev, Dockerfile.prod
        return "dockerfile"
    if name.startswith(".env"):  # .env.local, .env.production
        return "bash"
    return LANGUAGES.get(path.suffix.lower())


def extra_language(name: str):
    """Grammar + highlight query for a non-builtin language (registered on
    demand), or None if it isn't one / the grammar is unavailable."""
    if name == "r":
        from .rlang import R_HIGHLIGHTS, r_language

        grammar = r_language()
        return (grammar, R_HIGHLIGHTS) if grammar is not None else None
    from .configlang import CONFIG_LANGUAGES, config_grammar

    if name in CONFIG_LANGUAGES:
        grammar = config_grammar(name)
        return (grammar, CONFIG_LANGUAGES[name][1]) if grammar is not None else None
    return None


# Files above this size open in a read-only, truncated "large file" view so a
# huge log never freezes the editor or exhausts memory.
LARGE_FILE_BYTES = 10 * 1024 * 1024
LARGE_HEAD_BYTES = 4 * 1024 * 1024
HEAD_LINES = 10_000
_BINARY_SNIFF = 8192


def human_size(n: int) -> str:
    step = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if step < 1024 or unit == "TB":
            return f"{step:.0f} {unit}" if unit == "B" else f"{step:.1f} {unit}"
        step /= 1024
    return f"{n} B"


def classify_file(path: Path) -> tuple[str, int]:
    """Cheaply classify a file for routing: ('normal'|'large'|'binary', size).
    Only stats the size and sniffs the first few KB for NUL bytes."""
    try:
        size = path.stat().st_size
    except OSError:
        return "normal", 0
    try:
        with path.open("rb") as handle:
            head = handle.read(_BINARY_SNIFF)
    except OSError:
        return "normal", size
    # UTF-16 text is full of NUL bytes; a BOM means it's text, not binary.
    utf16 = head[:2] in (b"\xff\xfe", b"\xfe\xff")
    if not utf16 and b"\x00" in head:
        return "binary", size
    if size > LARGE_FILE_BYTES:
        return "large", size
    return "normal", size


def decode_text(data: bytes) -> tuple[str, str]:
    """Decode file bytes losslessly, returning (text, encoding).

    Honours a UTF-8 or UTF-16 byte-order mark, then tries UTF-8, and finally
    falls back to latin-1 — which maps every one of the 256 byte values, so it
    round-trips *any* byte sequence back to the exact same bytes on save. That
    means a non-UTF-8 file is never silently corrupted; at worst it shows as
    mojibake, and re-encoding with the recorded encoding writes it back intact.
    """
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", "replace"), "utf-8-sig"
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            return data.decode("utf-16"), "utf-16"
        except UnicodeDecodeError:
            pass
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return data.decode("latin-1"), "latin-1"


def detect_newline(text: str) -> str:
    """The file's dominant line ending, preserved across an edit/save cycle."""
    if "\r\n" in text:
        return "\r\n"
    if "\r" in text:
        return "\r"
    return "\n"


def normalize_newlines(text: str) -> str:
    """Collapse CR/CRLF to LF for the in-memory buffer (Textual expects LF);
    the original ending is recorded separately and restored on save."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write `data` to `path` atomically: a temp file in the same directory,
    fsync'd, then renamed over the target. A crash, full disk, or kill
    mid-write leaves the original file intact rather than truncated."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp_path = Path(tmp)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:  # keep the existing file's permission bits (mkstemp is 0600)
            os.chmod(tmp_path, stat.S_IMODE(path.stat().st_mode))
        except OSError:
            pass
        os.replace(tmp_path, path)
    except BaseException:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


def read_file_head(
    path: Path, force_full: bool = False
) -> tuple[str, str, int, str, str]:
    """Read a file for display, guarding huge and binary files.

    Returns (text, kind, size, encoding, newline) where kind is 'normal',
    'large' (only the head was read), or 'binary' (not shown). `text` is
    newline-normalised to LF; `encoding` and `newline` record how to write it
    back unchanged. With `force_full`, a large text file is read whole and
    reported 'normal' (the pager's open-in-editor escape hatch); binary files
    are still guarded.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return "", "normal", 0, "utf-8", "\n"
    if force_full:
        to_read = size
    else:
        to_read = size if size <= LARGE_FILE_BYTES else min(size, LARGE_HEAD_BYTES)
    with path.open("rb") as handle:
        data = handle.read(to_read)
    # A UTF-16 BOM means the NUL bytes are text, not a binary signature.
    utf16 = data[:2] in (b"\xff\xfe", b"\xfe\xff")
    if not utf16 and b"\x00" in data[:_BINARY_SNIFF]:
        placeholder = f"[binary file — {human_size(size)} — not shown]\n"
        return placeholder, "binary", size, "utf-8", "\n"
    raw_text, encoding = decode_text(data)
    newline = detect_newline(raw_text)
    text = normalize_newlines(raw_text)
    if not force_full and size > LARGE_FILE_BYTES:
        head = "\n".join(text.split("\n")[:HEAD_LINES])
        return head + "\n", "large", size, encoding, newline
    return text, "normal", size, encoding, newline


# Meta (M-) commands: key -> editor action. Dispatched from _on_key so they
# work both for real alt+key events (which carry a printable character that
# TextArea would otherwise self-insert) and for ESC-prefixed sequences, which
# many terminals send as a separate escape key followed by the base key.
META_ACTIONS: dict[str, str] = {
    "f": "cursor_word_right",
    "b": "cursor_word_left",
    "v": "cursor_page_up",
    "d": "kill_word",
    "w": "copy_region",
    "y": "yank_pop",
    "backspace": "kill_word_backward",
    "less_than_sign": "buffer_home",
    "greater_than_sign": "buffer_end",
    "up": "move_lines_up",
    "down": "move_lines_down",
    "x": "app.command_palette",  # M-x (also via ESC x)
    "semicolon": "toggle_comment",  # M-;
    "percent_sign": "app.query_replace",  # M-%
    "g": "app.goto_line",  # M-g
    "q": "fill_paragraph",  # M-q
}

# Line-comment prefix per language (M-;). Languages without a line-comment
# syntax are absent and M-; refuses politely.
COMMENT_PREFIXES: dict[str, str] = {
    "python": "#",
    "r": "#",
    "bash": "#",
    "yaml": "#",
    "toml": "#",
    "javascript": "//",
    "go": "//",
    "rust": "//",
    "java": "//",
    "sql": "--",
}


class EditorBuffer(TextArea):
    """A text editing buffer, optionally backed by a file."""

    BINDINGS = [
        # Movement.
        Binding("ctrl+f", "cursor_right", "forward-char", show=False),
        Binding("ctrl+b", "cursor_left", "backward-char", show=False),
        Binding("ctrl+n", "cursor_down", "next-line", show=False),
        Binding("ctrl+p", "cursor_up", "previous-line", show=False),
        Binding("ctrl+v", "cursor_page_down", "scroll-up", show=False),
        Binding("ctrl+home", "buffer_home", "beginning-of-buffer", show=False),
        Binding("ctrl+end", "buffer_end", "end-of-buffer", show=False),
        # Mark and region.
        Binding("ctrl+@", "set_mark", "set-mark", show=False),
        # Kill ring.
        Binding("ctrl+k", "kill_line", "kill-line", show=False),
        Binding("ctrl+w", "kill_region", "kill-region", show=False),
        Binding("ctrl+y", "yank", "yank", show=False),
        Binding("ctrl+backspace", "kill_word_backward", "backward-kill-word", show=False),
        # Undo (C-/ arrives as ctrl+underscore).
        Binding("ctrl+underscore", "undo", "undo", show=False),
        # Incremental search.
        Binding("ctrl+s", "isearch_forward", "isearch", show=False),
        Binding("ctrl+r", "isearch_backward", "isearch-backward", show=False),
    ]

    def __init__(self, path: Path | None = None, text: str = "", **kwargs) -> None:
        super().__init__(
            text,
            language=None,
            theme="github_light",
            show_line_numbers=True,
            soft_wrap=False,
            tab_behavior="indent",
            **kwargs,
        )
        self.path = path
        self.modified = False
        self._saved_text = text
        self.mark: tuple[int, int] | None = None
        self.mark_active = False
        # Command chaining, emacs-style: consecutive kills accumulate in one
        # ring entry, and M-y only works right after a yank.
        self._pending: str | None = None
        self._last_command: str | None = None
        self._yank_start: tuple[int, int] | None = None
        self._yank_end: tuple[int, int] | None = None
        self._meta = False  # one-shot Meta prefix set by a bare ESC
        self.disk_mtime: float | None = None  # mtime of path when last synced
        # Large / binary file guarding.
        self.large = False
        self.truncated = False  # large: only the head was loaded
        self.binary = False
        self.file_size = 0
        # How to write the buffer back exactly as it came in.
        self.encoding = "utf-8"
        self.newline = "\n"
        # Other views of the same buffer (C-x 2 / C-x 3 of the same file).
        self.links: list["EditorBuffer"] = []
        self._syncing = False
        # Incremental-search highlight: every occurrence of this query in the
        # visible lines is styled (the current match still rides the selection).
        self._search_highlight = ""
        self._apply_language()

    # -- search highlight ----------------------------------------------------

    SEARCH_STYLE = Style(bgcolor="yellow", color="black")

    def set_search_highlight(self, query: str) -> None:
        """Highlight every occurrence of `query` in the visible area (live,
        during isearch). Pass '' to clear."""
        if query == self._search_highlight:
            return
        self._search_highlight = query
        # The line cache key doesn't know about the query, so stale strips
        # would hide the change — drop them and repaint. (_line_cache is a
        # Textual internal; degrade to a plain repaint if it disappears.)
        cache = getattr(self, "_line_cache", None)
        if cache is not None:
            cache.clear()
        self.refresh()

    def clear_search_highlight(self) -> None:
        self.set_search_highlight("")

    def get_line(self, line_index: int):  # type: ignore[override]
        text = super().get_line(line_index)
        query = self._search_highlight
        if not query:
            return text
        # Smart case, matching isearch: an all-lowercase query folds case.
        fold = query == query.lower()
        line = text.plain
        hay = line.lower() if fold else line
        needle = query.lower() if fold else query
        n = len(needle)
        start = 0
        while n:
            idx = hay.find(needle, start)
            if idx == -1:
                break
            text.stylize(self.SEARCH_STYLE, idx, idx + n)
            start = idx + n
        return text

    @property
    def display_name(self) -> str:
        return self.path.name if self.path else "*untitled*"

    def restore_position(self, row: int, col: int, scroll: float) -> None:
        """Place the cursor and scroll (clamped) — for session restore."""
        row = max(0, min(row, self.document.line_count - 1))
        col = max(0, min(col, len(self.document.get_line(row))))
        self.selection = Selection((row, col), (row, col))
        self.scroll_y = max(0.0, scroll)

    # -- shared buffer (linked views) ---------------------------------------

    def edit(self, edit):  # type: ignore[override]
        """Apply an edit, then replay it to every linked view so windows on
        the same buffer stay in sync while keeping their own cursors."""
        result = super().edit(edit)
        if self.links and not self._syncing:
            for view in self.links:
                view._syncing = True
                try:
                    view.replace(
                        edit.text,
                        edit.from_location,
                        edit.to_location,
                        maintain_selection_offset=True,
                    )
                finally:
                    view._syncing = False
        return result

    def make_linked_view(self) -> "EditorBuffer":
        """A second view of this buffer: same file and text, shared edits,
        independent cursor and scroll."""
        peers = [self, *self.links]
        view = EditorBuffer(path=self.path, text=self.text)
        view._saved_text = self._saved_text
        view.modified = self.modified
        view.disk_mtime = self.disk_mtime
        for peer in peers:
            peer.links.append(view)
        view.links.extend(peers)
        return view

    def unlink(self) -> None:
        """Detach this view from its buffer's other views (on close/kill)."""
        for peer in self.links:
            peer.links.remove(self)
        self.links.clear()

    def _sync_saved(self, saved_text: str, mtime: float | None) -> None:
        """Propagate a save/reload baseline to a linked view."""
        self._saved_text = saved_text
        self.disk_mtime = mtime
        self.modified = self.text != saved_text

    def _apply_language(self) -> None:
        language = language_for(self.path)
        if language is not None and language not in self.available_languages:
            if (extra := extra_language(language)) is not None:
                self.register_language(language, *extra)
        if language in self.available_languages:
            self.language = language

    def _set_file_flags(self, kind: str, size: int) -> None:
        was_guarded = self.binary or self.large
        self.binary = kind == "binary"
        self.large = kind == "large"
        self.truncated = self.large
        self.file_size = size
        # A truncated/binary view must be read-only — saving it would write
        # only the head (or a placeholder) back over the real file. Lift the
        # guard again when a reload brings the buffer back to a normal, fully
        # loaded state (a user-toggled read-only is otherwise left alone).
        guarded = self.binary or self.large
        if guarded:
            self.read_only = True
        elif was_guarded:
            self.read_only = False

    def load(self, path: Path, force_full: bool = False) -> None:
        self.path = path
        text, kind, size, encoding, newline = read_file_head(
            path, force_full=force_full
        )
        self.encoding = encoding
        self.newline = newline
        self._set_file_flags(kind, size)
        self._syncing = True
        try:
            self.text = text
        finally:
            self._syncing = False
        self.modified = False
        self._saved_text = self.text
        try:
            self.disk_mtime = path.stat().st_mtime
        except OSError:
            self.disk_mtime = None
        if self.binary or self.large or (force_full and size > LARGE_FILE_BYTES):
            self.language = None  # skip highlighting a huge/binary buffer
        else:
            self._apply_language()

    def reload_from_disk(self) -> None:
        """Re-read the file into this view and every linked view, each keeping
        its own cursor and scroll position (clamped)."""
        if self.path is None or not self.path.exists():
            return
        text, kind, size, encoding, newline = read_file_head(self.path)
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            mtime = None
        for view in [self, *self.links]:
            view._reload_text(text, mtime, kind, size, encoding, newline)

    def _reload_text(
        self,
        text: str,
        mtime: float | None,
        kind: str = "normal",
        size: int = 0,
        encoding: str = "utf-8",
        newline: str = "\n",
    ) -> None:
        self.encoding = encoding
        self.newline = newline
        self._set_file_flags(kind, size)
        sel = self.selection
        scroll = self.scroll_y
        self._syncing = True  # wholesale replace, don't forward as an edit
        try:
            self.text = text
        finally:
            self._syncing = False
        self.modified = False
        self._saved_text = text
        self.disk_mtime = mtime
        self.mark_active = False
        last_row = self.document.line_count - 1

        def clamp(location: tuple[int, int]) -> tuple[int, int]:
            row = min(location[0], last_row)
            return row, min(location[1], len(self.document.get_line(row)))

        self.selection = Selection(clamp(sel.start), clamp(sel.end))
        self.scroll_to(y=scroll, animate=False)

    def save(self, path: Path | None = None) -> Path:
        """Write the buffer to disk; returns the path written."""
        if self.truncated or self.binary:
            # This view holds only the head (or a placeholder) — writing it
            # would truncate/corrupt the file on disk.
            raise ValueError("refusing to save a large or binary view")
        if path is not None:
            self.path = path
            self._apply_language()
        if self.path is None:
            raise ValueError("buffer has no file name")
        text = self.text
        if self.newline != "\n":
            text = text.replace("\n", self.newline)
        try:
            data = text.encode(self.encoding)
        except UnicodeEncodeError as error:
            raise ValueError(
                f"can't save as {self.encoding}: {error.reason} "
                f"(character {error.object[error.start:error.end]!r})"
            ) from error
        atomic_write_bytes(self.path, data)
        self.modified = False
        self._saved_text = self.text
        try:
            self.disk_mtime = self.path.stat().st_mtime
        except OSError:
            self.disk_mtime = None
        for view in self.links:  # linked views share the saved baseline
            view.path = self.path
            view._sync_saved(self.text, self.disk_mtime)
        return self.path

    def writable(self) -> bool:
        """False (with a warning) when the buffer is read-only; edit actions
        must check this — TextArea.read_only only blocks direct typing."""
        if self.read_only:
            self.app.notify(
                "Buffer is read-only (C-x C-q to toggle)",
                severity="warning",
                timeout=2,
            )
            return False
        return True

    def _on_text_area_changed(self, event: TextArea.Changed) -> None:
        # Runs before the message bubbles to the app, so the app sees the
        # up-to-date modified state. A buffer whose text matches what is on
        # disk is not modified, even after a programmatic load().
        self.modified = self.text != self._saved_text

    async def _on_key(self, event: events.Key) -> None:
        self._last_command, self._pending = self._pending, None
        # ESC is the Meta prefix, as in emacs: it makes the next key an M- key.
        if event.key == "escape":
            self._meta = True
            self._pending = self._last_command  # don't break kill/yank chains
            event.stop()
            event.prevent_default()
            return
        meta_key: str | None = None
        if self._meta:
            self._meta = False
            meta_key = event.key
        elif event.key.startswith("alt+"):
            meta_key = event.key[4:]
        if meta_key is not None:
            # Never let TextArea self-insert the base character of an M- key.
            event.stop()
            event.prevent_default()
            if (action := META_ACTIONS.get(meta_key)) is not None:
                await self.run_action(action)
            return
        # Markdown smart editing: Enter continues list/quote markers, Tab
        # moves between table cells and nests list items.
        if self.language == "markdown" and not self.read_only:
            handled = False
            if event.key == "enter":
                handled = self._markdown_enter()
            elif event.key in ("tab", "shift+tab"):
                handled = self._markdown_tab(backward=event.key == "shift+tab")
            if handled:
                event.stop()
                event.prevent_default()
                return
        # Emacs: typing while the region is active inserts at point without
        # deleting the region (no delete-selection-mode).
        if self.mark_active and event.is_printable:
            self.deactivate_mark()
        await super()._on_key(event)

    # -- mark and region -----------------------------------------------------

    @property
    def point(self) -> tuple[int, int]:
        return self.selection.end

    def action_set_mark(self) -> None:
        cur = self.point
        self.mark = cur
        self.mark_active = True
        self.selection = Selection(cur, cur)
        self.app.notify("Mark set", timeout=1)

    def deactivate_mark(self) -> None:
        self.mark_active = False
        cur = self.point
        self.selection = Selection(cur, cur)

    def exchange_point_and_mark(self) -> None:
        if self.mark is None:
            self.app.notify("No mark set in this buffer", severity="warning")
            return
        old_mark = self.mark
        self.mark = self.point
        self.mark_active = True
        self.selection = Selection(self.mark, old_mark)
        self.scroll_cursor_visible()

    def mark_whole_buffer(self) -> None:
        """C-x h: point at start, mark (active) at end."""
        self.mark = self.document.end
        self.mark_active = True
        self.selection = Selection(self.document.end, (0, 0))
        self.scroll_cursor_visible()

    # Movement extends the region while the mark is active.

    def _sel(self, select: bool) -> bool:
        return select or self.mark_active

    def action_cursor_right(self, select: bool = False) -> None:
        super().action_cursor_right(self._sel(select))

    def action_cursor_left(self, select: bool = False) -> None:
        super().action_cursor_left(self._sel(select))

    def action_cursor_down(self, select: bool = False) -> None:
        super().action_cursor_down(self._sel(select))

    def action_cursor_up(self, select: bool = False) -> None:
        super().action_cursor_up(self._sel(select))

    def action_cursor_word_right(self, select: bool = False) -> None:
        super().action_cursor_word_right(self._sel(select))

    def action_cursor_word_left(self, select: bool = False) -> None:
        super().action_cursor_word_left(self._sel(select))

    def action_cursor_line_start(self, select: bool = False) -> None:
        super().action_cursor_line_start(self._sel(select))

    def action_cursor_line_end(self, select: bool = False) -> None:
        super().action_cursor_line_end(self._sel(select))

    def action_cursor_page_down(self) -> None:
        if self.mark_active:
            self.move_cursor_relative(rows=self.content_size.height, select=True)
        else:
            super().action_cursor_page_down()

    def action_cursor_page_up(self) -> None:
        if self.mark_active:
            self.move_cursor_relative(rows=-self.content_size.height, select=True)
        else:
            super().action_cursor_page_up()

    def action_buffer_home(self) -> None:
        self.move_cursor((0, 0), select=self.mark_active)

    def action_buffer_end(self) -> None:
        self.move_cursor(self.document.end, select=self.mark_active)

    # -- kill ring -------------------------------------------------------------

    def _kill_range(
        self, start: tuple[int, int], end: tuple[int, int], *, backward: bool = False
    ) -> None:
        start, end = sorted((start, end))
        text = self.get_text_range(start, end)
        if not text:
            return
        ring = self.app.kill_ring
        if self._last_command == "kill":
            ring.add_to_top(text, before=backward)
        else:
            ring.push(text)
        self.delete(start, end)
        self._pending = "kill"

    def action_kill_line(self) -> None:
        if not self.writable():
            return
        row, col = self.point
        line = self.document.get_line(row)
        if col < len(line):
            end = (row, len(line))
        elif row + 1 < self.document.line_count:
            end = (row + 1, 0)
        else:
            return
        self._kill_range(self.point, end)

    def action_kill_region(self) -> None:
        if not self.writable():
            return
        sel = self.selection
        if sel.is_empty:
            self.app.notify("The region is empty", severity="warning", timeout=2)
            return
        self._kill_range(sel.start, sel.end)
        self.mark_active = False

    def action_copy_region(self) -> None:
        sel = self.selection
        if sel.is_empty:
            self.app.notify("The region is empty", severity="warning", timeout=2)
            return
        start, end = sorted((sel.start, sel.end))
        text = self.get_text_range(start, end)
        self.app.kill_ring.push(text)
        clipboard.copy_explicit(self.app, text)
        self.deactivate_mark()

    def _word_range(self, *, backward: bool) -> tuple[tuple[int, int], tuple[int, int]]:
        """The range from point to the next word boundary, via the cursor
        actions so word rules match ordinary movement."""
        origin = self.point
        self.selection = Selection(origin, origin)
        if backward:
            TextArea.action_cursor_word_left(self, select=True)
        else:
            TextArea.action_cursor_word_right(self, select=True)
        target = self.selection.end
        self.selection = Selection(origin, origin)
        return origin, target

    def action_kill_word(self) -> None:
        if not self.writable():
            return
        start, end = self._word_range(backward=False)
        self._kill_range(start, end)

    def action_kill_word_backward(self) -> None:
        if not self.writable():
            return
        start, end = self._word_range(backward=True)
        self._kill_range(start, end, backward=True)

    def action_yank(self) -> None:
        if not self.writable():
            return
        text = self.app.kill_ring.current
        if text is None:
            self.app.notify("Kill ring is empty", severity="warning", timeout=2)
            return
        cur = self.point
        self.selection = Selection(cur, cur)
        result = self.insert(text, cur)
        self._yank_start, self._yank_end = cur, result.end_location
        self.mark = cur  # emacs leaves the mark at the start of yanked text
        self.mark_active = False
        self._pending = "yank"

    def action_yank_pop(self) -> None:
        if not self.writable():
            return
        if self._last_command != "yank" or self._yank_start is None:
            self.app.notify("Previous command was not a yank", severity="warning", timeout=2)
            return
        text = self.app.kill_ring.rotate()
        if text is None:
            return
        self.delete(self._yank_start, self._yank_end)
        result = self.insert(text, self._yank_start)
        self._yank_end = result.end_location
        self._pending = "yank"

    # -- line moving (M-up / M-down) --------------------------------------------

    def _selected_rows(self) -> tuple[int, int]:
        """First and last row spanned by the selection (or the cursor line).
        A selection ending at column 0 does not include that final line."""
        (r0, c0), (r1, c1) = sorted((self.selection.start, self.selection.end))
        if r1 > r0 and c1 == 0:
            r1 -= 1
        return r0, r1

    def _move_lines(self, offset: int) -> None:
        if not self.writable():
            return
        first, last = self._selected_rows()
        document = self.document
        if offset < 0 and first == 0:
            return
        if offset > 0 and last >= document.line_count - 1:
            return
        block = [document.get_line(row) for row in range(first, last + 1)]
        if offset < 0:
            other = document.get_line(first - 1)
            lines = block + [other]
            start, end = (first - 1, 0), (last, len(block[-1]))
        else:
            other = document.get_line(last + 1)
            lines = [other] + block
            start, end = (first, 0), (last + 1, len(other))
        sel = self.selection
        self.replace("\n".join(lines), start, end, maintain_selection_offset=False)
        # Keep the selection (and mark) on the moved block.
        moved = Selection(
            (sel.start[0] + offset, sel.start[1]), (sel.end[0] + offset, sel.end[1])
        )
        self.selection = moved
        if self.mark is not None and first <= self.mark[0] <= last:
            self.mark = (self.mark[0] + offset, self.mark[1])
        self.scroll_cursor_visible()

    def action_move_lines_up(self) -> None:
        was_active = self.mark_active
        self._move_lines(-1)
        self.mark_active = was_active

    def action_move_lines_down(self) -> None:
        was_active = self.mark_active
        self._move_lines(1)
        self.mark_active = was_active

    # -- comment toggle (M-;) ----------------------------------------------------

    def action_toggle_comment(self) -> None:
        if not self.writable():
            return
        prefix = COMMENT_PREFIXES.get(self.language or "")
        if prefix is None:
            self.app.notify(
                f"No line comment for {self.language or 'plain text'}",
                severity="warning",
                timeout=2,
            )
            return
        first, last = self._selected_rows()
        document = self.document
        lines = [document.get_line(row) for row in range(first, last + 1)]
        content = [line for line in lines if line.strip()]
        if not content:
            return
        if all(line.lstrip().startswith(prefix) for line in content):
            # Uncomment: strip the prefix (and one following space).
            new_lines = []
            for line in lines:
                if line.strip():
                    at = line.index(prefix)
                    rest = line[at + len(prefix) :]
                    new_lines.append(line[:at] + rest.removeprefix(" "))
                else:
                    new_lines.append(line)
        else:
            indent = min(len(line) - len(line.lstrip()) for line in content)
            new_lines = [
                line[:indent] + prefix + " " + line[indent:] if line.strip() else line
                for line in lines
            ]
        was_active = self.mark_active
        sel = self.selection
        self.replace(
            "\n".join(new_lines),
            (first, 0),
            (last, len(lines[-1])),
            maintain_selection_offset=False,
        )
        # Restore the selection, clamping columns to the edited lines.
        def clamp(location: tuple[int, int]) -> tuple[int, int]:
            row, col = location
            if first <= row <= last:
                col = min(col, len(new_lines[row - first]))
            return row, col

        self.selection = Selection(clamp(sel.start), clamp(sel.end))
        self.mark_active = was_active

    # -- markdown smart editing -------------------------------------------------

    def _doc_lines(self) -> list[str]:
        document = self.document
        return [document.get_line(i) for i in range(document.line_count)]

    def _replace_keeping_view(self, text: str, start, end) -> None:
        """Replace a range without letting the edit's end-of-text cursor
        yank the viewport; the caller sets the real cursor afterwards (and
        watch_selection scrolls it visible if it truly moved away)."""
        x, y = self.scroll_x, self.scroll_y
        self.replace(text, start, end, maintain_selection_offset=False)
        self.scroll_to(x=x, y=y, animate=False)

    def _markdown_enter(self) -> bool:
        """Smart Enter: continue list/quote markers, end a list on an empty
        item, auto-close a just-opened code fence. True when handled."""
        if not self.selection.is_empty:
            return False
        row, col = self.point
        lines = self._doc_lines()
        if markdown.in_fence(lines, row):
            return False
        line = lines[row]
        if col == len(line) and (
            closer := markdown.unclosed_fence_opener(lines, row)
        ) is not None:
            self.replace("\n\n" + closer, (row, col), (row, col),
                         maintain_selection_offset=False)
            self.selection = Selection((row + 1, 0), (row + 1, 0))
            return True
        prefix = markdown.continuation(line)
        if prefix is None:
            return False
        if markdown.is_empty_item(line) and col == len(line):
            # Enter on an empty item ends the list: remove the marker.
            self.replace("", (row, 0), (row, len(line)),
                         maintain_selection_offset=False)
            self.selection = Selection((row, 0), (row, 0))
            self._markdown_restart_tail(row, markdown.parse_item(line))
            return True
        if col < markdown.content_col(line):
            return False  # inside the marker itself: a plain newline
        # Splitting mid-item: the text after point moves to the new item, so
        # swallow the whitespace under the cursor rather than carrying it.
        rest = line[col:]
        skip = len(rest) - len(rest.lstrip(" "))
        self._replace_keeping_view("\n" + prefix, (row, col), (row, col + skip))
        target = (row + 1, len(prefix))
        self.selection = Selection(target, target)
        item = markdown.parse_item(line)
        if item is not None and markdown.ORDERED_RE.match(item.marker):
            self._markdown_renumber(row + 1)
        return True

    def _markdown_restart_tail(self, row: int, item) -> None:
        """After an empty ordered item is removed mid-list, restart the list
        below at the removed number, so the source numbering matches what a
        renderer shows (a blank line doesn't end a markdown list)."""
        if item is None or (m := markdown.ORDERED_RE.match(item.marker)) is None:
            return
        if row + 1 >= self.document.line_count:
            return
        line = self.document.get_line(row + 1)
        nxt = markdown.parse_item(line)
        if (
            nxt is None
            or nxt.quote != item.quote
            or len(nxt.indent) != len(item.indent)
            or (m2 := markdown.ORDERED_RE.match(nxt.marker)) is None
        ):
            return
        new = (nxt.quote + nxt.indent + m["number"] + m2["delim"]
               + nxt.space + (nxt.box or "") + nxt.content)
        if new != line:
            sel = self.selection
            self._replace_keeping_view(new, (row + 1, 0), (row + 1, len(line)))
            self.selection = sel
            self._markdown_renumber(row + 1)

    def _markdown_renumber(self, row: int) -> None:
        """Renumber the ordered-list block around `row`, keeping the cursor
        on the same spot of its (possibly re-widthed) line."""
        lines = self._doc_lines()
        result = markdown.renumber(lines, row)
        if result is None:
            return
        start, end, new = result
        sel_row, sel_col = self.point
        self._replace_keeping_view("\n".join(new), (start, 0), (end, len(lines[end])))
        col = sel_col
        if start <= sel_row <= end:
            col = max(0, sel_col + len(new[sel_row - start]) - len(lines[sel_row]))
        self.selection = Selection((sel_row, col), (sel_row, col))

    def _markdown_tab(self, backward: bool) -> bool:
        """Tab/Shift-Tab: cell navigation inside a table, indent/outdent on
        a list item. True when handled (Tab on a list line is always
        swallowed so it never injects spaces mid-item)."""
        if not self.selection.is_empty:
            return False
        row, col = self.point
        lines = self._doc_lines()
        if markdown.in_fence(lines, row):
            return False
        line = lines[row]
        if markdown.is_table_row(line):
            if self.writable():
                self._table_tab(lines, row, col, backward)
            return True
        if markdown.parse_item(line) is None:
            return False
        if not self.writable():
            return True
        new = (markdown.outdent_item if backward else markdown.indent_item)(lines, row)
        if new is not None and new != line:
            spot = (row, max(0, col + len(new) - len(line)))
            self._replace_keeping_view(new, (row, 0), (row, len(line)))
            self.selection = Selection(spot, spot)
            self._markdown_renumber(row)
        return True

    def _table_tab(self, lines: list[str], row: int, col: int, backward: bool) -> None:
        """Align the table at `row`, then move to the next/previous cell;
        Tab off the last cell appends a fresh empty row."""
        idx = markdown.cell_index(lines[row], col)
        result = markdown.align_table(lines, row)
        if result is None:
            return
        start, end, table = result
        if table != lines[start : end + 1]:
            self._replace_keeping_view("\n".join(table), (start, 0), (end, len(lines[end])))
        # Every data cell in reading order: (absolute row, content column).
        cells: list[tuple[int, int]] = []
        flat_of: dict[tuple[int, int], int] = {}
        for i, table_line in enumerate(table):
            if markdown.is_separator_row(table_line):
                continue
            for j, (s, _) in enumerate(markdown.cell_bounds(table_line)):
                flat_of[(start + i, j)] = len(cells)
                cells.append((start + i, s))
        if not cells:
            return
        if (row, idx) in flat_of:
            target = flat_of[(row, idx)] + (-1 if backward else 1)
        elif backward:  # from the separator row: previous row's last cell
            earlier = [p for (r, _), p in flat_of.items() if r < row]
            target = earlier[-1] if earlier else 0
        else:  # from the separator row: next row's first cell
            later = [p for (r, _), p in flat_of.items() if r > row]
            target = later[0] if later else len(cells)
        if target < 0:
            target = 0
        if target >= len(cells):
            if backward:
                target = len(cells) - 1
            else:
                new_row = markdown.blank_row_like(table[-1])
                end_col = len(table[-1])
                self._replace_keeping_view("\n" + new_row, (end, end_col), (end, end_col))
                bounds = markdown.cell_bounds(new_row)
                dest = (end + 1, bounds[0][0] if bounds else 0)
                self.selection = Selection(dest, dest)
                return
        dest = cells[target]
        self.selection = Selection(dest, dest)

    def action_fill_paragraph(self) -> None:
        """M-q: refill the paragraph/list item/quote at point to
        `fill_column`, or align the pipe table the cursor is in."""
        if not self.writable():
            return
        if self.language not in (None, "markdown"):
            self.app.notify(
                f"fill-paragraph is for prose, not {self.language}",
                severity="warning",
                timeout=2,
            )
            return
        width = int(config.load()["fill_column"])
        lines = self._doc_lines()
        row, col = self.point
        result = markdown.reformat(lines, row, width)
        if result is None:
            self.app.notify("Nothing to fill here", timeout=2)
            return
        start, end, new, what = result
        if new == lines[start : end + 1]:
            return
        # Keep the cursor on the same character through the reflow.
        new_row, new_col = markdown.remap_point(
            lines[start : end + 1], new, row - start, col
        )
        self._replace_keeping_view("\n".join(new), (start, 0), (end, len(lines[end])))
        spot = (start + new_row, new_col)
        self.selection = Selection(spot, spot)
        self.app.notify(
            "Aligned table" if what == "table" else "Filled paragraph", timeout=1.5
        )

    def markdown_toggle_checkbox(self) -> None:
        """C-c C-t: flip [ ]/[x] on a task item; add a box to a plain item."""
        if not self.writable():
            return
        if self.language != "markdown":
            self.app.notify("Not a markdown buffer", severity="warning", timeout=2)
            return
        row, col = self.point
        line = self.document.get_line(row)
        new = markdown.toggle_checkbox(line)
        if new is None:
            self.app.notify("Not a list item", severity="warning", timeout=2)
            return
        self.replace(new, (row, 0), (row, len(line)),
                     maintain_selection_offset=False)
        spot = (row, min(col, len(new)))
        self.selection = Selection(spot, spot)

    def markdown_emphasis(self, kind: str) -> None:
        """C-c b/i/c: toggle bold/italic/code on the region, or on the word
        at point when no region is active."""
        if not self.writable():
            return
        if self.language != "markdown":
            self.app.notify("Not a markdown buffer", severity="warning", timeout=2)
            return
        sel = self.selection
        if not sel.is_empty:
            start, end = sorted((sel.start, sel.end))
        else:
            row, col = self.point
            span = markdown.word_at(self.document.get_line(row), col)
            if span is None:
                self.app.notify("No word at point", severity="warning", timeout=2)
                return
            start, end = (row, span[0]), (row, span[1])
        text = self.get_text_range(start, end)
        result = self.replace(markdown.toggle_emphasis(text, kind), start, end,
                              maintain_selection_offset=False)
        self.mark_active = False
        self.selection = Selection(result.end_location, result.end_location)

    async def _on_paste(self, event: events.Paste) -> None:
        # Pasting a URL over selected markdown text turns it into a link.
        if (
            self.language == "markdown"
            and not self.read_only
            and not self.selection.is_empty
            and markdown.is_url(event.text)
        ):
            start, end = sorted((self.selection.start, self.selection.end))
            label = self.get_text_range(start, end)
            if "\n" not in label and label.strip():
                event.stop()
                event.prevent_default()
                result = self.replace(
                    f"[{label}]({event.text.strip()})", start, end,
                    maintain_selection_offset=False,
                )
                self.mark_active = False
                self.selection = Selection(result.end_location, result.end_location)
                return
        await super()._on_paste(event)

    # -- isearch ---------------------------------------------------------------

    def action_isearch_forward(self) -> None:
        from .isearch import ISearchScreen

        self.app.push_screen(ISearchScreen(self, forward=True))

    def action_isearch_backward(self) -> None:
        from .isearch import ISearchScreen

        self.app.push_screen(ISearchScreen(self, forward=False))
