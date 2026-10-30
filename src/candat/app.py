"""Candat: a terminal text editor with emacs keybindings, built on Textual."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.css.query import NoMatches
from textual.widgets import DirectoryTree, Static, TabbedContent, TextArea

from .buffers import BufferListScreen
from . import clipboard, codeblocks, config, recovery, session, watchdog
from .chords import CTRL_C_MAP, CTRL_X_MAP, ChordScreen
from .commands import CandatCommands
from .bigtable import BigTable, has_quoted_newline
from .csvview import (  # CSV_SUFFIXES: default set; config can extend
    CSV_SUFFIXES,
    MAX_ROWS,
    sniff_sample,
)
from .dialogs import ConfirmScreen, PromptScreen
from .docconvert import ConversionError, convert_to_markdown, is_document
from .editor import (
    HEAD_LINES,
    HIGHLIGHT_MAX_BYTES,
    LARGE_FILE_BYTES,
    EditorBuffer,
    classify_file,
    human_size,
)
from .help import HelpScreen
from .killring import KillRing
from .nav import DEFAULT_TREE_WIDTH, MIN_TREE_WIDTH, FileTree, NavPanel, TreeSplitter
from .pager import TextPager
from .pane import BufferPane, pane_of, unique_labels
from .window import MAX_GROUPS, EditorGroup, group_of
from .preview import PREVIEW_MODES, MarkdownPreview
from .projectsearch import SearchResultsScreen, search_project
from .replace import QueryReplaceScreen
from .terminal import TerminalPane
from .theme import CANDAT_LIGHT


# Table files estimated above this many cells open in the windowed BigTable:
# the classic table loads, searches and filters on the UI thread.
CLASSIC_MAX_CELLS = 150_000

SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
# Operations shorter than this never show an indicator, so the common
# quick case (a small preview render, a short search) doesn't flicker.
BUSY_DELAY = 0.3


class StatusBar(Static):
    """One-line status: buffer name, modified flag, cursor position, language
    — plus a spinner while something long-running is going on, so a
    conversion, a big render or a project search never looks like a freeze."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._base = ""
        self._busy: dict[str, str] = {}  # key -> message, in start order
        self._pending: dict[str, object] = {}  # key -> delay timer
        self._spinner_timer = None
        self._frame = 0

    def update(self, content="", *, layout: bool = True) -> None:  # type: ignore[override]
        self._base = content
        super().update(self._status_text(), layout=layout)

    def _status_text(self) -> str:
        if not self._busy:
            return str(self._base)
        frame = SPINNER[self._frame % len(SPINNER)]
        what = ", ".join(self._busy.values())
        return f"{self._base}   [b $warning]{frame} {what}[/]"

    def begin(self, key: str, message: str, delay: float = BUSY_DELAY) -> None:
        """Show `message` with a spinner until end(key) — after `delay`
        seconds, so an operation that finishes quickly shows nothing."""
        self.end(key)
        if delay <= 0:
            self._start(key, message)
        else:
            self._pending[key] = self.set_timer(delay, lambda: self._start(key, message))

    def _start(self, key: str, message: str) -> None:
        self._pending.pop(key, None)
        self._busy[key] = message
        if self._spinner_timer is None:
            self._spinner_timer = self.set_interval(0.1, self._tick)
        super().update(self._status_text())

    def _tick(self) -> None:
        self._frame += 1
        super().update(self._status_text())

    def end(self, key: str) -> None:
        if (timer := self._pending.pop(key, None)) is not None:
            timer.stop()  # type: ignore[attr-defined]
        if self._busy.pop(key, None) is None:
            return
        if not self._busy and self._spinner_timer is not None:
            self._spinner_timer.stop()
            self._spinner_timer = None
        super().update(self._status_text())

    @property
    def busy_with(self) -> list[str]:
        """Messages currently shown (for tests)."""
        return list(self._busy.values())

    def show(self, editor: EditorBuffer | None) -> None:
        if editor is None:
            self.update(" candat")
            return
        # Emacs-style flags: %% read-only, ** modified, -- clean.
        if editor.read_only:
            flag = "%%"
        elif editor.modified:
            flag = "**"
        else:
            flag = "--"
        row, col = editor.cursor_location
        language = editor.language or "text"
        where = str(editor.path) if editor.path else editor.display_name
        wrap = "  wrap" if editor.soft_wrap else ""
        # Surface anything that isn't plain UTF-8 / LF, so a round-trip that
        # will rewrite the file in another encoding or line ending is visible.
        enc = "" if editor.encoding == "utf-8" else f"  {editor.encoding}"
        eol = "  CRLF" if editor.newline == "\r\n" else ("  CR" if editor.newline == "\r" else "")
        if editor.binary:
            big = "  [b]binary[/]"
        elif editor.document_view:
            big = "  [b]document → markdown[/]"
        elif editor.truncated:
            big = "  [b]large: head only[/]"
        else:
            big = ""
        self.update(
            f" {flag} {where}   Ln {row + 1}, Col {col + 1}   {language}{enc}{eol}{wrap}{big}"
            "   [dim]F1 help[/]"
        )


class CandatApp(App[None]):
    TITLE = "candat"
    COMMAND_PALETTE_BINDING = "ctrl+shift+p"
    COMMANDS = App.COMMANDS | {CandatCommands}

    CSS = """
    #workspace {
        height: 1fr;
    }
    /* The TreeSplitter draws the divider (and is the drag handle). */
    NavPanel.-hidden,
    TreeSplitter.-hidden {
        display: none;
    }
    #groups {
        width: 1fr;
        layout: horizontal;
    }
    #groups.-stacked {
        layout: vertical;
    }
    EditorGroup {
        width: 1fr;
        height: 1fr;
    }
    #groups.-split EditorGroup {
        border: round $panel;
    }
    #groups.-split EditorGroup:focus-within {
        border: round $primary;
    }
    TabbedContent {
        width: 1fr;
    }
    TabPane {
        padding: 0;
    }
    EditorBuffer {
        border: none;
        width: 1fr;
    }
    MarkdownPreview {
        display: none;
        width: 1fr;
        border-left: solid $panel;
        background: $background;
        padding: 0 1;
    }
    TabPane.-preview-split MarkdownPreview,
    TabPane.-preview-only MarkdownPreview {
        display: block;
    }
    TabPane.-preview-only EditorBuffer {
        display: none;
    }
    CsvViewer {
        display: none;
    }
    TabPane.-csv-table CsvViewer {
        display: block;
    }
    TabPane.-csv-table EditorBuffer {
        display: none;
    }
    Welcome {
        display: none;
        width: 1fr;
        height: 1fr;
        content-align: center middle;
    }
    TabPane.-welcome Welcome {
        display: block;
    }
    TabPane.-welcome EditorBuffer {
        display: none;
    }
    TextPager {
        display: none;
        width: 1fr;
        padding: 0 1;
    }
    TabPane.-pager TextPager {
        display: block;
    }
    TabPane.-pager EditorBuffer {
        display: none;
    }
    BigTable {
        display: none;
        width: 1fr;
    }
    TabPane.-bigtable BigTable {
        display: block;
    }
    TabPane.-bigtable EditorBuffer {
        display: none;
    }
    StatusBar {
        dock: bottom;
        height: 1;
        background: $panel;
        color: $foreground;
    }
    """

    BINDINGS = [
        Binding("ctrl+x", "chord_prefix", "C-x", priority=True, show=False),
        Binding("ctrl+g", "keyboard_quit", "C-g", priority=True, show=False),
        # C-c is a prefix (mode commands), never Textual's quit.
        Binding("ctrl+c", "chord_prefix_cc", show=False, priority=True),
        # Claim ctrl+q from Textual's default quit so C-x C-q can reach the
        # chord screen (and a stray C-q never kills the app).
        Binding("ctrl+q", "keyboard_quit", show=False, priority=True),
        Binding("alt+x", "command_palette", "M-x", show=False),
        Binding("f1", "help", "help", show=False),
    ]

    def __init__(self, paths: list[Path] | None = None) -> None:
        super().__init__()
        # In system_clipboard = "all" mode every kill mirrors to the system
        # clipboard; the hook itself decides based on the config.
        self.kill_ring = KillRing(on_push=lambda text: clipboard.copy_on_kill(self, text))
        self.last_search = ""
        self._crash_log: Path | None = None
        paths = paths or []
        self._root = Path.cwd()
        dirs = [p for p in paths if p.is_dir()]
        if dirs:
            self._root = dirs[0]
        self._files = [p for p in paths if not p.is_dir()]
        self._buffer_count = 0
        # Crash-recovery autosave state (see _autosave_recovery).
        self._recovery_busy = False
        self._recovery_signature: tuple = ()
        self._quitting = False
        self._group_count = 1
        self._active_group: EditorGroup | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="workspace"):
            yield NavPanel(self._root)
            yield TreeSplitter()
            with Horizontal(id="groups"):
                yield EditorGroup(id="group-1")
        yield TerminalPane()
        yield StatusBar()

    async def on_mount(self) -> None:
        self.register_theme(CANDAT_LIGHT)
        self.theme = "candat-light"
        if not config.load()["tree_visible"]:
            self._set_tree_visible(False, persist=False)
        if self._files:
            for path in self._files:
                await self._open_path(path)
        elif not await self._restore_session():
            # Nothing to show: the untitled buffer opens as a welcome screen
            # (typing or opening a file dismisses it).
            await self._new_buffer()
            if (pane := self.active_pane) is not None:
                pane.enter_welcome_mode()
            self._refresh_status()
        self.set_interval(1.0, self._check_disk_changes)
        self.set_interval(20.0, self._autosave_recovery)
        self._watchdog = watchdog.start_from_env()  # CANDAT_WATCHDOG=1
        self._announce_recovery()

    # -- crash recovery ------------------------------------------------------

    def _dirty_snapshots(self) -> list[tuple[Path | None, str]]:
        """(path, text) for every buffer with unsaved edits, de-duplicated
        across linked views."""
        out: list[tuple[Path | None, str]] = []
        seen: set = set()
        for editor in self.all_editors():
            if not editor.modified:
                continue
            key = editor.path or id(editor)
            if key in seen:
                continue
            seen.add(key)
            out.append((editor.path, editor.text))
        return out

    def _autosave_recovery(self) -> None:
        """Snapshot dirty buffers to the recovery dir; drop the snapshots when
        nothing is dirty, so a saved-and-clean editor leaves nothing behind.

        The file I/O runs on a worker thread (a slow disk or network home
        must not stall typing); buffers unchanged since the last snapshot
        are skipped, and a tick is skipped while the previous write runs."""
        if self._recovery_busy or self._quitting:
            return
        dirty = [editor for editor in self.all_editors() if editor.modified]
        signature = tuple((id(editor), editor.revision) for editor in dirty)
        if signature == self._recovery_signature:
            return
        self._recovery_signature = signature
        snapshots = self._dirty_snapshots()
        self._recovery_busy = True

        def write() -> None:
            try:
                if snapshots:
                    recovery.snapshot(snapshots)
                else:
                    recovery.clear()
            finally:
                self._recovery_busy = False

        self.run_worker(write, thread=True, group="recovery")

    def _announce_recovery(self) -> None:
        leftover = recovery.pending()
        if leftover:
            self.notify(
                f"{len(leftover)} unsaved file(s) from a previous session were "
                f"recovered to {recovery.recovery_dir()}",
                severity="warning",
                timeout=8,
            )

    # -- session restore -----------------------------------------------------

    async def _restore_session(self) -> bool:
        """Reopen this project root's last session (files, cursors, active
        tab); returns whether anything was reopened."""
        if not config.load()["restore_session"]:
            return False
        saved = session.load(self._root)
        if not saved:
            return False
        entries = [
            entry
            for entry in saved.get("files", [])
            if isinstance(entry, dict) and Path(str(entry.get("path", ""))).exists()
        ]
        if not entries:
            return False
        for entry in entries:
            path = Path(entry["path"])
            await self._open_path(path)
            editor = self._plain_editor_for(path)
            if editor is not None:
                editor.restore_position(
                    int(entry.get("row", 0)),
                    int(entry.get("col", 0)),
                    float(entry.get("scroll", 0.0)),
                )
        if active := saved.get("active"):
            self._activate_path(Path(active))
        self._refresh_status()
        return True

    def _plain_editor_for(self, path: Path) -> EditorBuffer | None:
        """The text-editor buffer showing `path`, if it isn't a CSV/pager
        view (those manage their own position)."""
        for pane in self.query(BufferPane):
            if pane.editor.path == path and not pane.is_csv and not pane.is_pager:
                return pane.editor
        return None

    def _activate_path(self, path: Path) -> None:
        for pane in self.query(BufferPane):
            if pane.editor.path == path:
                group = group_of(pane)
                if group is not None and pane.id is not None:
                    group.active = pane.id
                    self._active_group = group
                pane.focus_visible()
                return

    def _save_session(self) -> None:
        files: list[dict] = []
        seen: set[str] = set()
        for pane in self.query(BufferPane):
            editor = pane.editor
            if editor.path is None:
                continue
            key = str(editor.path)
            if key in seen:
                continue  # linked views (C-x 2/3) of the same buffer
            seen.add(key)
            row, col = editor.cursor_location
            files.append(
                {"path": key, "row": row, "col": col, "scroll": float(editor.scroll_y)}
            )
        pane = self.active_pane
        active = (
            str(pane.editor.path) if pane is not None and pane.editor.path else None
        )
        session.save(self._root, files, active)

    def exit(self, *args, **kwargs) -> None:  # every quit path funnels here
        self._quitting = True  # no new recovery snapshots after this
        if getattr(self, "_watchdog", None) is not None:
            self._watchdog.stop()
        try:
            self._save_session()
            recovery.clear()  # a clean quit needs no crash recovery
        except Exception:
            pass  # never let bookkeeping block quitting
        super().exit(*args, **kwargs)

    # -- windows (editor groups) -------------------------------------------

    def groups(self) -> list[EditorGroup]:
        return list(self.query(EditorGroup))

    @property
    def active_group(self) -> EditorGroup:
        """The focused editor group; falls back to the first if the tracked
        one was closed."""
        if self._active_group is not None and self._active_group.is_mounted:
            return self._active_group
        self._active_group = self.query(EditorGroup).first()
        return self._active_group

    @property
    def tabs(self) -> EditorGroup:
        """The active group. Kept named `tabs` so buffer operations read the
        same as before splits existed."""
        return self.active_group

    @on(events.DescendantFocus)
    def _track_active_group(self, event: events.DescendantFocus) -> None:
        group = group_of(event.widget)
        if group is not None and group is not self._active_group:
            self._active_group = group
            self._refresh_status()

    # -- buffer bookkeeping ------------------------------------------------

    @property
    def active_pane(self) -> BufferPane | None:
        return self.active_group.active_pane  # every pane we add is a BufferPane

    @property
    def active_editor(self) -> EditorBuffer | None:
        pane = self.active_pane
        return pane.editor if pane is not None else None

    def panes(self) -> list[BufferPane]:
        """Buffers in the active group (for the buffer list)."""
        return list(self.active_group.query(BufferPane))

    def all_editors(self) -> list[EditorBuffer]:
        """Every buffer across all groups (for disk-watch, quit, kill)."""
        return [pane.editor for pane in self.query(BufferPane)]

    async def _add_pane(self, editor: EditorBuffer) -> BufferPane:
        """Wrap an editor in a BufferPane and add it to the active group."""
        self._buffer_count += 1
        pane = BufferPane(editor, f"buffer-{self._buffer_count}")
        await self.tabs.add_pane(pane)
        # Linked preview: follow the editor's scroll position.
        self.watch(editor, "scroll_y", pane.sync_preview_scroll, init=False)
        self.tabs.active = pane.id
        editor.focus()
        self._refresh_tab_labels()
        if editor.language == "markdown" and not editor.document_view:
            await pane.set_preview_mode(session.view_mode(editor.path))
        return pane

    async def _new_buffer(
        self, path: Path | None = None, load_text: bool = True
    ) -> EditorBuffer:
        editor = EditorBuffer(path=None)
        if path is not None and path.exists() and load_text:
            await self._load_text(editor, path)
        else:
            editor.path = path
            editor._apply_language()
        await self._add_pane(editor)
        return editor

    async def _load_text(
        self, editor: EditorBuffer, path: Path, force_full: bool = False
    ) -> None:
        """editor.load(), announcing it first for a big file: building the
        document (and the first frame) is UI-thread work that can take
        seconds, so the status bar says "Loading…" before it starts rather
        than the app just stalling."""
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        if size > HIGHLIGHT_MAX_BYTES:
            status = self.query_one(StatusBar)
            status.begin("load", f"Loading {path.name} ({human_size(size)})…", delay=0)
            await asyncio.sleep(0.05)  # let that frame paint
            try:
                editor.load(path, force_full=force_full)
            finally:
                status.end("load")
            if not editor.binary and not editor.large and editor.language is None:
                self.notify(
                    f"Large file ({human_size(size)}) — syntax highlighting off",
                    timeout=4,
                )
        else:
            editor.load(path, force_full=force_full)
        self._announce_file(editor)

    def _announce_file(self, editor: EditorBuffer) -> None:
        if editor.binary:
            self.notify(
                f"Binary file, not shown — {human_size(editor.file_size)}",
                severity="warning",
                timeout=4,
            )
        elif editor.truncated:
            self.notify(
                f"Large file ({human_size(editor.file_size)}) — read-only, "
                f"showing the first {HEAD_LINES:,} lines",
                severity="warning",
                timeout=5,
            )

    async def _open_path(self, path: Path) -> None:
        path = path.expanduser().resolve()
        if path.is_dir():
            self.notify(f"{path} is a directory", severity="warning")
            return
        # CSV/TSV files open in the table viewer; big text files in the pager;
        # neither loads the file into the editor. Word-processor documents
        # are converted to markdown and shown rendered, read-only.
        is_csv = path.suffix.lower() in self._table_suffixes() and path.exists()
        is_doc = is_document(path) and path.exists()
        kind = (
            classify_file(path)[0] if (path.exists() and not is_csv and not is_doc) else "normal"
        )
        is_large = kind == "large"
        # Already open in any window? Switch to that window and tab.
        for pane in self.query(BufferPane):
            if pane.editor.path == path:
                group = group_of(pane)
                if group is not None and pane.id is not None:
                    group.active = pane.id
                    self._active_group = group
                pane.focus_visible()
                return
        # Reuse a pristine untitled buffer instead of stacking new tabs.
        current = self.active_pane
        if (
            current is not None
            and current.editor.path is None
            and not current.editor.modified
            and not current.editor.text
        ):
            current.leave_welcome_mode()
            editor = current.editor
            if is_doc:
                if not await self._show_document(current, path):
                    editor.load(path)  # unreadable document: the binary view
                    self._announce_file(editor)
                    self._refresh_tab_label(editor)
                    self._refresh_status()
                return
            if path.exists() and not is_csv and not is_large:
                await self._load_text(editor, path)
            else:
                editor.path = path
                editor._apply_language()
            self._refresh_tab_label(editor)
            if is_csv:
                self._enter_table(current, path)
                return
            if is_large:
                self._enter_pager(current, path)
                return
            self._refresh_status()
            if editor.language == "markdown":
                await current.set_preview_mode(session.view_mode(editor.path))
            if current.preview_mode != "only":
                editor.focus()
            return
        if is_doc:
            editor = await self._new_buffer(path, load_text=False)
            pane = pane_of(editor)
            if pane is not None and not await self._show_document(pane, path):
                editor.load(path)
                self._announce_file(editor)
                self._refresh_tab_label(editor)
                self._refresh_status()
            return
        editor = await self._new_buffer(path, load_text=not is_csv and not is_large)
        pane = pane_of(editor)
        if is_csv and pane is not None:
            self._enter_table(pane, path)
        elif is_large and pane is not None:
            self._enter_pager(pane, path)
        if not path.exists():
            self.notify("(new file)", timeout=2)

    async def _show_document(self, pane: BufferPane, path: Path) -> bool:
        """Convert a .docx/.odt into the pane's editor and show the rendered
        markdown (preview-only; `C-c C-v` cycles to the markdown source).
        Returns False when the file couldn't be converted."""
        editor = pane.editor
        editor.path = path  # so a second open of the file finds this tab
        self._refresh_tab_label(editor)
        status = self.query_one(StatusBar)
        status.begin(f"convert-{pane.id}", f"Converting {path.name}…", delay=0)
        try:
            text = await asyncio.to_thread(convert_to_markdown, path)
        except ConversionError as error:
            self.notify(str(error), severity="error", timeout=5)
            return False
        finally:
            status.end(f"convert-{pane.id}")
        if not pane.is_mounted:
            return True  # closed while converting; nothing to show
        editor.set_document(path, text)
        self._refresh_tab_label(editor)
        await pane.set_preview_mode("only")
        self._refresh_status()
        return True

    def _wants_bigtable(self, path: Path) -> bool:
        """True when a file needs the windowed (unlimited-row) table: too big
        for the classic DataTable by size or estimated row count. Files whose
        sample shows quoted multi-line fields stay on the classic table — the
        windowed index is line-based."""
        try:
            size = path.stat().st_size
            with path.open("rb") as handle:
                sample = handle.read(8192).decode("utf-8", "replace")
        except OSError:
            return False
        if has_quoted_newline(sample):
            return False
        lines = sample.count("\n") or 1
        est_rows = size / max(1, len(sample) / lines)
        # Only grids that are cheap to handle whole (about a second for 150k
        # cells) stay on the classic table.
        delimiter, _ = sniff_sample(sample, path.suffix.lower())
        first = sample.split("\n", 1)[0]
        est_cells = est_rows * (first.count(delimiter) + 1)
        return (
            size > LARGE_FILE_BYTES
            or est_rows > MAX_ROWS
            or est_cells > CLASSIC_MAX_CELLS
        )

    def _enter_table(self, pane: BufferPane, path: Path) -> None:
        """Table view for a file: windowed for huge files, classic otherwise."""
        if self._wants_bigtable(path):
            pane.enter_bigtable_mode(path)
        else:
            pane.enter_csv_mode(path)
        self._refresh_status()

    def _table_suffixes(self) -> set[str]:
        """Suffixes that open straight into the table viewer (configurable via
        `table_suffixes`; defaults to .csv/.tsv)."""
        values = config.load()["table_suffixes"]
        suffixes = {
            str(v).lower() for v in values if isinstance(v, str) and v.startswith(".")
        }
        return suffixes or CSV_SUFFIXES

    def _enter_pager(self, pane: BufferPane, path: Path) -> None:
        pane.enter_pager_mode(path)
        try:
            size = human_size(path.stat().st_size)
        except OSError:
            size = "?"
        self.notify(f"Large file ({size}) — pager", timeout=4)
        self._refresh_status()

    def _refresh_tab_label(self, editor: EditorBuffer) -> None:
        """Relabel every tab: a label depends on the *other* open files too
        (same-named files get their directory prepended), so one buffer's
        change can rename its namesakes in other tabs and windows."""
        self._refresh_tab_labels()

    def _refresh_tab_labels(self) -> None:
        panes = list(self.query(BufferPane))
        labels = unique_labels([pane.editor.path for pane in panes])
        for pane, label in zip(panes, labels):
            group = group_of(pane)
            if pane.id is None or group is None:
                continue
            if pane.editor.modified:
                label += "*"
            try:
                tab = group.get_tab(pane.id)
            except NoMatches:  # pane not (yet / any more) in a tab bar
                continue
            if tab.label_text != label:
                tab.label = label

    def _refresh_status(self) -> None:
        status = self.query_one(StatusBar)
        pane = self.active_pane
        if pane is not None and pane.is_pager:
            where = pane.editor.path or pane.editor.display_name
            status.update(f" %% {where}   {pane.pager.status()}   [dim]F1 help[/]")
            self.sub_title = str(pane.editor.path or "")
            return
        if pane is not None and pane.is_bigtable:
            where = pane.bigtable.path or pane.editor.display_name
            status.update(f" %% {where}   big table   [dim]F1 help[/]")
            self.sub_title = str(pane.bigtable.path or "")
            return
        if pane is not None and pane.is_welcome:
            status.update(
                " candat — start typing, or C-x C-f to open a file   [dim]F1 help[/]"
            )
            self.sub_title = ""
            return
        editor = self.active_editor
        status.show(editor)
        self.sub_title = str(editor.path) if editor and editor.path else ""

    @on(MarkdownPreview.Busy)
    def _preview_busy(self, event: MarkdownPreview.Busy) -> None:
        status = self.query_one(StatusBar)
        key = f"preview-{id(event.preview)}"
        if event.busy:
            status.begin(key, "Rendering preview…")
        else:
            status.end(key)

    @on(TextPager.Moved)
    def _pager_moved(self, event: TextPager.Moved) -> None:
        self._refresh_status()

    @on(BigTable.Moved)
    def _bigtable_moved(self, event: BigTable.Moved) -> None:
        self._refresh_status()

    def _schedule_preview(self, editor: EditorBuffer) -> None:
        """Debounced live preview refresh while editing markdown."""
        pane = pane_of(editor)
        if pane is None or pane.preview_mode == "off":
            return
        if timer := getattr(editor, "_preview_timer", None):
            timer.stop()
        preview = pane.preview
        # The render runs off the UI thread, but it still costs CPU that
        # competes with typing — a larger document waits for a longer pause.
        delay = min(2.0, 0.3 + editor.document.line_count / 20_000)
        editor._preview_timer = self.set_timer(
            delay, lambda: preview.render_text(editor.text)
        )

    def _toggle_csv_view(self, pane: BufferPane) -> None:
        """Switch a buffer between table view and text. Clean file-backed
        buffers stream from disk (cheap for huge files); a modified or
        untitled buffer is parsed from its own text, so the table always
        shows what you see. From the pager, the table streams the same file;
        leaving the table on a large file returns to the pager rather than
        loading gigabytes into the editor."""
        editor = pane.editor
        if pane.is_pager:
            # Pager -> table: both views stream, so size is no obstacle.
            path = pane.pager.path
            if path is None:
                return
            pane.leave_pager_mode()
            self._enter_table(pane, path)
            return
        if pane.is_bigtable:
            # Big table -> back to the pager (the file is huge by definition).
            path = pane.bigtable.path
            pane.leave_bigtable_mode()
            if path is not None:
                self._enter_pager(pane, path)
            else:
                editor.focus()
                self._refresh_status()
            return
        if pane.is_csv:
            size = 0
            if editor.path is not None and editor.path.exists():
                size = editor.path.stat().st_size
            if editor.disk_mtime is None and size > LARGE_FILE_BYTES:
                # Table -> pager for a large file the editor never loaded.
                pane.leave_csv_mode()
                self._enter_pager(pane, editor.path)
                return

            async def to_text() -> None:
                if (
                    editor.path is not None
                    and editor.disk_mtime is None
                    and editor.path.exists()
                ):
                    await self._load_text(editor, editor.path)
                    self._refresh_tab_label(editor)
                pane.leave_csv_mode()
                editor.focus()
                self._refresh_status()

            if editor.disk_mtime is None and size > 5_000_000:
                async def maybe(confirmed: bool | None) -> None:
                    if confirmed:
                        await to_text()

                self.push_screen(
                    ConfirmScreen(
                        f"{editor.display_name} is {size / 1_000_000:.0f} MB; "
                        "load as text?"
                    ),
                    maybe,
                )
            else:
                self.run_worker(to_text(), exclusive=False)
            return
        # Entering table view.
        if editor.binary:
            self.notify("Not a text buffer", severity="warning", timeout=2)
            return
        if editor.path is not None and not editor.modified:
            if self._wants_bigtable(editor.path):
                pane.enter_bigtable_mode(editor.path)
                self._refresh_status()
                return
            try:
                mtime = editor.path.stat().st_mtime
            except OSError:
                mtime = None
            if pane.csv._path != editor.path or pane.csv.mtime != mtime:
                pane.enter_csv_mode(editor.path)
            else:
                pane.show_table()
        else:
            pane.enter_csv_mode_text(editor.text, editor.display_name)
        self._refresh_status()

    def action_toggle_preview(self) -> None:
        """C-c C-v: the buffer's alternate view — markdown cycles its preview,
        everything else toggles the table view (any delimiter; `d` inside the
        table re-picks it). Works from the pager too: both views stream."""
        pane = self.active_pane
        if pane is None:
            return
        if (
            pane.is_pager
            or pane.is_csv
            or pane.is_bigtable
            or pane.editor.language != "markdown"
        ):
            self._toggle_csv_view(pane)
            return
        next_mode = PREVIEW_MODES[
            (PREVIEW_MODES.index(pane.preview_mode) + 1) % len(PREVIEW_MODES)
        ]
        self.call_later(pane.set_preview_mode, next_mode)
        if not pane.editor.document_view:
            # New markdown files (and this one, next time) open in this view.
            session.save_view_mode(pane.editor.path, next_mode)

    def action_table_view(self) -> None:
        """M-x table-view: toggle the table even where C-c C-v means
        something else (a markdown buffer)."""
        pane = self.active_pane
        if pane is None:
            return
        self._toggle_csv_view(pane)

    # -- events --------------------------------------------------------------

    @on(DirectoryTree.FileSelected)
    async def _tree_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        await self._open_path(event.path)

    @on(TextArea.Changed)
    def _text_changed(self, event: TextArea.Changed) -> None:
        editor = event.text_area
        if isinstance(editor, EditorBuffer):
            self._refresh_tab_label(editor)
            if editor.language == "markdown":
                self._schedule_preview(editor)
            # A welcome buffer that gains text (however it happens) is a
            # scratch buffer now.
            pane = pane_of(editor)
            if pane is not None and pane.is_welcome and editor.text:
                pane.leave_welcome_mode()
                editor.focus()
        self._refresh_status()

    @on(TextArea.SelectionChanged)
    def _selection_changed(self, event: TextArea.SelectionChanged) -> None:
        self._refresh_status()

    @on(TabbedContent.TabActivated)
    def _tab_activated(self, event: TabbedContent.TabActivated) -> None:
        self._refresh_status()

    # -- actions (dispatched directly or via C-x chords) ---------------------

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action in ("chord_prefix", "chord_prefix_cc", "keyboard_quit"):
            # While a modal (chord, prompt, confirm) is up, let it see these
            # keys instead of the app's priority bindings.
            if self.screen is not self.screen_stack[0]:
                return False
            # A focused terminal gets C-c and C-g raw (interrupting the shell
            # matters more); C-x stays reserved as the way out.
            if action != "chord_prefix" and isinstance(self.focused, TerminalPane):
                return False
        return True

    def action_chord_prefix(self) -> None:
        self.push_screen(ChordScreen("C-x", CTRL_X_MAP))

    def action_chord_prefix_cc(self) -> None:
        self.push_screen(ChordScreen("C-c", CTRL_C_MAP))

    def action_keyboard_quit(self) -> None:
        """C-g: cancel whatever is pending — a modal screen or an active mark."""
        if len(self.screen_stack) > 1:
            self.pop_screen()
            return
        pane = self.active_pane
        if pane is not None and pane.is_pager and pane.pager.following:
            pane.pager.stop_follow()
            self.notify("Follow: off", timeout=1.5)
            return
        if pane is not None and pane.is_pager and pane.pager.searching:
            pane.pager.cancel_search()
            return
        if pane is not None and pane.is_csv and pane.csv.searching:
            pane.csv.cancel_search()
            return
        if pane is not None and pane.is_bigtable and pane.bigtable.searching:
            pane.bigtable.cancel_search()
            return
        if isinstance(self.focused, MarkdownPreview) and self.focused.clear_mark():
            return
        editor = self.active_editor
        if editor is not None and editor.mark_active:
            editor.deactivate_mark()

    def action_exchange_point_and_mark(self) -> None:
        if (editor := self.active_editor) is not None:
            editor.exchange_point_and_mark()

    def action_mark_whole_buffer(self) -> None:
        if (editor := self.active_editor) is not None:
            editor.mark_whole_buffer()

    def action_undo_buffer(self) -> None:
        if (editor := self.active_editor) is not None and editor.writable():
            editor.undo()

    def action_comment_dwim(self) -> None:
        if (editor := self.active_editor) is not None:
            editor.action_toggle_comment()

    def action_fill_paragraph(self) -> None:
        if (editor := self.active_editor) is not None:
            editor.action_fill_paragraph()

    def action_markdown_toggle_checkbox(self) -> None:
        if (editor := self.active_editor) is not None:
            editor.markdown_toggle_checkbox()

    def action_markdown_bold(self) -> None:
        if (editor := self.active_editor) is not None:
            editor.markdown_emphasis("bold")

    def action_markdown_italic(self) -> None:
        if (editor := self.active_editor) is not None:
            editor.markdown_emphasis("italic")

    def action_markdown_code(self) -> None:
        if (editor := self.active_editor) is not None:
            editor.markdown_emphasis("code")

    def action_toggle_soft_wrap(self) -> None:
        pane = self.active_pane
        if pane is not None and pane.is_pager:
            wrapped = pane.pager.toggle_wrap()
            self._refresh_status()
            self.notify(f"Pager: {'wrap' if wrapped else 'no-wrap'}", timeout=1.5)
            return
        editor = self.active_editor
        if editor is None:
            return
        editor.soft_wrap = not editor.soft_wrap
        self._refresh_status()
        self.notify(
            f"Soft wrap {'on' if editor.soft_wrap else 'off'}", timeout=1.5
        )

    def action_toggle_read_only(self) -> None:
        editor = self.active_editor
        if editor is None:
            return
        editor.read_only = not editor.read_only
        self._refresh_status()
        state = "read-only" if editor.read_only else "writable"
        self.notify(f"{editor.display_name} is now {state}", timeout=2)

    def action_find_file_read_only(self) -> None:
        editor = self.active_editor
        base = editor.path.parent if editor and editor.path else self._root
        initial = str(base) + "/"

        async def opened(result: str | None) -> None:
            if result:
                await self._open_path(Path(result))
                if (opened_editor := self.active_editor) is not None:
                    opened_editor.read_only = True
                    self._refresh_status()

        self.push_screen(
            PromptScreen("Find file read-only:", initial, complete_paths=True), opened
        )

    # -- disk watching ---------------------------------------------------------

    def _check_disk_changes(self) -> None:
        """Poll open files for external changes: clean buffers reload in
        place; edited buffers get asked before their edits are discarded."""
        if len(self.screen_stack) > 1:
            return  # don't interrupt prompts, searches, or chords
        for editor in self.all_editors():
            path = editor.path
            if path is None:
                continue
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue  # deleted or unreadable; keep the buffer as-is
            pane = pane_of(editor)
            if pane is not None and pane.is_csv:
                # Table view watches the file itself; the text was never
                # loaded, so the editor path below does not apply.
                viewer = pane.csv
                if viewer.mtime is not None and mtime != viewer.mtime:
                    viewer.reload()
                    self.notify(f"Reloaded {editor.display_name}", timeout=1.5)
                continue
            if pane is not None and pane.is_pager:
                # The pager watches the file itself: growth extends the index
                # in place (viewport stays put); rotation/truncation reopens.
                if pane.pager.check_disk() == "reloaded":
                    self.notify(f"Reloaded {editor.display_name}", timeout=1.5)
                continue
            if pane is not None and pane.is_bigtable:
                if pane.bigtable.check_disk() == "reloaded":
                    self.notify(f"Reloaded {editor.display_name}", timeout=1.5)
                continue
            if editor.disk_mtime is None:
                continue  # text never loaded (e.g. CSV toggled but unloaded)
            if mtime == editor.disk_mtime:
                continue
            if not editor.modified:
                self._reload(editor)
            else:
                # Remember this change so declining doesn't re-prompt every
                # second; the next *further* disk change asks again.
                editor.disk_mtime = mtime

                def reload_if_confirmed(
                    confirmed: bool | None, editor: EditorBuffer = editor
                ) -> None:
                    if confirmed:
                        self._reload(editor)

                self.push_screen(
                    ConfirmScreen(
                        f"{editor.display_name} changed on disk; "
                        "reload and discard your edits?"
                    ),
                    reload_if_confirmed,
                )
                return  # one question at a time

    def _reload(self, editor: EditorBuffer) -> None:
        if editor.document_view:
            self.run_worker(self._reload_document(editor), exclusive=False)
            return
        editor.reload_from_disk()
        self._reloaded(editor)

    async def _reload_document(self, editor: EditorBuffer) -> None:
        """Re-convert a changed .docx/.odt in a worker thread, then install
        the markdown in every view of it."""
        path = editor.path
        if path is None:
            return
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return
        # Claim this change now so the 1 s disk poll doesn't start a second
        # conversion while this one runs.
        for view in [editor, *editor.links]:
            view.disk_mtime = mtime
        status = self.query_one(StatusBar)
        status.begin(f"convert-{id(editor)}", f"Converting {path.name}…", delay=0)
        try:
            text = await asyncio.to_thread(convert_to_markdown, path)
        except ConversionError as error:
            self.notify(f"{path.name} changed but can't be converted: {error}", severity="warning")
            return
        finally:
            status.end(f"convert-{id(editor)}")
        if not editor.is_mounted:
            return
        for view in [editor, *editor.links]:
            view._reload_text(text, mtime, "normal", view.file_size, "utf-8", "\n")
        self._reloaded(editor)

    def _reloaded(self, editor: EditorBuffer) -> None:
        self._refresh_tab_label(editor)
        self._refresh_status()
        if editor.language == "markdown":
            self._schedule_preview(editor)
        self.notify(f"Reloaded {editor.display_name}", timeout=1.5)

    def action_isearch_forward(self) -> None:
        if self._pager_search(forward=True):
            return
        if (editor := self.active_editor) is not None:
            editor.action_isearch_forward()

    def action_isearch_backward(self) -> None:
        if self._pager_search(forward=False):
            return
        if (editor := self.active_editor) is not None:
            editor.action_isearch_backward()

    def _pager_search(self, forward: bool) -> bool:
        """C-s/C-r in the pager: repeat to the next/previous match if a search
        is active, else prompt for one (`/` `?` always start fresh). n / N also
        repeat. Returns True if handled (pager active)."""
        pane = self.active_pane
        if pane is None or not pane.is_pager:
            return False
        pager = pane.pager
        if pager.searching:
            pager.search_next(forward)
            self._refresh_status()
        else:
            self._pager_search_prompt(pager, forward)
        return True

    def action_pager_search_new(self, direction: int) -> None:
        """`/` `?` in the pager: always prompt for a new query."""
        pane = self.active_pane
        if pane is None or not pane.is_pager:
            return
        self._pager_search_prompt(pane.pager, direction > 0)

    def _pager_search_prompt(self, pager, forward: bool) -> None:
        def run(query: str | None) -> None:
            if query:
                pager.search(query, forward)
                self._refresh_status()

        prompt = "Search:" if forward else "Search backward:"
        self.push_screen(PromptScreen(prompt), run)

    def action_pager_goto_line(self) -> None:
        pane = self.active_pane
        if pane is None or not pane.is_pager:
            return
        pager = pane.pager

        def run(value: str | None) -> None:
            if value and value.strip().lstrip("-").isdigit():
                pager.goto_line(int(value.strip()) - 1)  # 1-based in the prompt
                self._refresh_status()

        self.push_screen(PromptScreen(f"Go to line (1-{pager.line_count}):"), run)

    def action_goto_line(self) -> None:
        """M-g: jump the editor cursor to a 1-based line number."""
        editor = self.active_editor
        if editor is None:
            return
        total = editor.document.line_count

        def run(value: str | None) -> None:
            if value and value.strip().lstrip("-").isdigit():
                row = max(0, min(int(value.strip()) - 1, total - 1))
                editor.move_cursor((row, 0), center=True)
                editor.focus()

        self.push_screen(PromptScreen(f"Go to line (1-{total}):"), run)

    def action_pager_open_in_editor(self) -> None:
        """`e` / `v` in the pager: load the whole file into a real editor
        buffer anyway (the pager is read-only by design)."""
        pane = self.active_pane
        if pane is None or not pane.is_pager:
            return
        path = pane.pager.path
        if path is None:
            return
        try:
            size = human_size(path.stat().st_size)
        except OSError as error:
            self.notify(f"Cannot read {path.name}: {error}", severity="error")
            return

        async def load(confirmed: bool | None) -> None:
            if not confirmed:
                return
            pane.leave_pager_mode()
            await self._load_text(pane.editor, path, force_full=True)
            self._refresh_tab_label(pane.editor)
            self._refresh_status()
            pane.editor.focus()

        self.push_screen(
            ConfirmScreen(f"Load all of {path.name} ({size}) into the editor?"),
            load,
        )

    def action_find_file(self) -> None:
        editor = self.active_editor
        base = editor.path.parent if editor and editor.path else self._root
        initial = str(base) + "/"

        async def opened(result: str | None) -> None:
            if result:
                await self._open_path(Path(result))

        self.push_screen(
            PromptScreen("Find file:", initial, complete_paths=True), opened
        )

    def action_save_buffer(self) -> None:
        pane = self.active_pane
        if pane is None:
            return
        if pane.is_csv:
            self.notify(
                "Table view is read-only — C-c C-v to edit as text",
                severity="warning",
                timeout=2,
            )
            return
        editor = pane.editor
        if editor.path is None:
            self.action_write_file()
            return
        self._save(editor, None)

    def action_write_file(self) -> None:
        editor = self.active_editor
        if editor is None:
            return
        base = editor.path if editor.path else self._root
        initial = str(base) if editor.path else str(base) + "/"

        def written(result: str | None) -> None:
            if result:
                self._save(editor, Path(result).expanduser().resolve())

        self.push_screen(
            PromptScreen("Write file:", initial, complete_paths=True), written
        )

    def _save(self, editor: EditorBuffer, path: Path | None) -> None:
        if editor.document_view and (path is None or path == editor.path):
            self.notify(
                "This is a converted view of a document — C-x C-w exports the "
                "markdown to another file; the document itself is never written.",
                severity="warning",
                timeout=5,
            )
            return
        if editor.truncated or editor.binary:
            self.notify(
                "This is a large/binary view — saving is disabled so it can't "
                "overwrite the file with a partial copy.",
                severity="warning",
                timeout=4,
            )
            return
        try:
            written = editor.save(path)
        except (OSError, ValueError) as error:
            self.notify(f"Save failed: {error}", severity="error")
            return
        self._refresh_tab_label(editor)
        for view in editor.links:  # linked views are now clean too
            self._refresh_tab_label(view)
        self._refresh_status()
        self.notify(f"Wrote {written}", timeout=2)

    def action_kill_buffer(self) -> None:
        editor = self.active_editor
        if editor is None:
            return

        async def maybe_kill(confirmed: bool | None) -> None:
            if confirmed:
                await self._kill(editor)

        if editor.modified:
            self.push_screen(
                ConfirmScreen(f"{editor.display_name} is modified; kill anyway?"),
                maybe_kill,
            )
        else:
            self.call_later(self._kill, editor)

    async def _kill(self, editor: EditorBuffer) -> None:
        group = group_of(editor) or self.active_group
        pane = pane_of(editor)
        editor.unlink()  # detach from other views of this buffer
        if pane is not None and pane.id is not None:
            await group.remove_pane(pane.id)
        # Keep every window non-empty: replace a killed last buffer with a
        # scratch buffer in that same window.
        if not list(group.query(BufferPane)):
            self._active_group = group
            await self._new_buffer()
        self._refresh_tab_labels()  # a namesake may drop its directory again
        self._refresh_status()

    def action_switch_buffer(self) -> None:
        """C-x b: pick a buffer from a list. The next buffer is preselected,
        so Enter-Enter still cycles like before."""
        panes = self.panes()
        if not panes:
            return
        entries: list[tuple[str, str]] = []
        active_index = 0
        for index, pane in enumerate(panes):
            editor = pane.editor
            label = editor.display_name + ("*" if editor.modified else "")
            if editor.path is not None:
                label = f"{label}  [dim]{editor.path}[/]"
            entries.append((pane.id or "", label))
            if pane is self.active_pane:
                active_index = index

        def switched(pane_id: str | None) -> None:
            if pane_id:
                self.tabs.active = pane_id
                if (pane := self.active_pane) is not None:
                    pane.focus_visible()

        preselect = (active_index + 1) % len(entries)
        self.push_screen(BufferListScreen(entries, preselect), switched)

    def action_other_window(self) -> None:
        """C-x o: cycle focus tree -> each window -> terminal (when open)."""
        tree = self.query_one(DirectoryTree)
        terminal = self.query_one(TerminalPane)
        ring: list = [] if self.query_one(NavPanel).has_class("-hidden") else [tree]
        for group in self.groups():
            if (pane := group.active_pane) is not None:
                ring.append(pane.visible_widget)
        if terminal.has_class("-open"):
            ring.append(terminal)
        if not ring:
            return
        focused = self.focused
        for index, widget in enumerate(ring):
            if focused is widget or (focused is not None and widget in focused.ancestors_with_self):
                ring[(index + 1) % len(ring)].focus()
                return
        ring[0].focus()

    # -- windows (split / delete) ------------------------------------------

    def _sync_split_class(self) -> None:
        self.query_one("#groups").set_class(len(self.groups()) > 1, "-split")

    async def _split(self, stacked: bool) -> None:
        if len(self.groups()) >= MAX_GROUPS:
            self.notify(f"At most {MAX_GROUPS} windows", severity="warning", timeout=2)
            return
        # Emacs shows the current buffer in the new window: a linked view,
        # sharing text and edits but with its own cursor and scroll.
        current = self.active_editor
        box = self.query_one("#groups")
        box.set_class(stacked, "-stacked")
        self._group_count += 1
        group = EditorGroup(id=f"group-{self._group_count}")
        await box.mount(group)
        self._active_group = group
        self._sync_split_class()
        if current is not None and current.path is not None:
            await self._add_pane(current.make_linked_view())
        else:
            await self._new_buffer()  # scratch when there's nothing to share

    def action_split_window_below(self) -> None:
        self.call_later(self._split, True)

    def action_split_window_right(self) -> None:
        self.call_later(self._split, False)

    async def _delete_window(self, victim: EditorGroup) -> None:
        survivor = next((g for g in self.groups() if g is not victim), None)
        if survivor is None:
            return
        self._active_group = survivor
        for pane in victim.query(BufferPane):
            pane.editor.unlink()  # its buffers live on in other windows
        await victim.remove()
        self._sync_split_class()
        if (pane := survivor.active_pane) is not None:
            pane.focus_visible()

    def _unsaved_in(self, group: EditorGroup) -> list[str]:
        return [
            p.editor.display_name
            for p in group.query(BufferPane)
            if p.editor.modified
        ]

    def action_delete_window(self) -> None:
        if len(self.groups()) <= 1:
            self.notify("Only one window", severity="warning", timeout=2)
            return
        victim = self.active_group
        unsaved = self._unsaved_in(victim)

        def maybe(confirmed: bool | None) -> None:
            if confirmed:
                self.call_later(self._delete_window, victim)

        if unsaved:
            self.push_screen(
                ConfirmScreen(f"Window has unsaved: {', '.join(unsaved)}. Close anyway?"),
                maybe,
            )
        else:
            self.call_later(self._delete_window, victim)

    async def _delete_other_windows(self, keep: EditorGroup) -> None:
        for group in self.groups():
            if group is not keep:
                for pane in group.query(BufferPane):
                    pane.editor.unlink()
                await group.remove()
        self._active_group = keep
        self._sync_split_class()
        if (pane := keep.active_pane) is not None:
            pane.focus_visible()

    def action_delete_other_windows(self) -> None:
        if len(self.groups()) <= 1:
            return
        keep = self.active_group
        unsaved = [
            name
            for group in self.groups()
            if group is not keep
            for name in self._unsaved_in(group)
        ]

        def maybe(confirmed: bool | None) -> None:
            if confirmed:
                self.call_later(self._delete_other_windows, keep)

        if unsaved:
            self.push_screen(
                ConfirmScreen(
                    f"Other windows have unsaved: {', '.join(unsaved)}. Close them?"
                ),
                maybe,
            )
        else:
            self.call_later(self._delete_other_windows, keep)

    def action_cycle_tree_icons(self) -> None:
        name = self.query_one(FileTree).cycle_icons()
        self.notify(f"File-tree icons: {name}", timeout=2)

    def action_copy_tree_path(self) -> None:
        self.query_one(FileTree).action_copy_path()

    async def action_refresh_tree(self) -> None:
        await self.query_one(FileTree).action_refresh()

    def set_tree_width(self, cells: int, persist: bool = True) -> int:
        """Resize the file-tree panel (drag splitter, C-x { / C-x }), clamped
        to [MIN_TREE_WIDTH, half the screen]; persisted to the config."""
        cells = max(MIN_TREE_WIDTH, min(int(cells), max(MIN_TREE_WIDTH, self.size.width // 2)))
        self.query_one(NavPanel).styles.width = cells
        if persist:
            config.save_setting("tree_width", cells)
        return cells

    def _nudge_tree(self, delta: int) -> None:
        current = self.query_one(NavPanel).outer_size.width
        width = self.set_tree_width(current + delta)
        self.notify(f"Tree width: {width}", timeout=1.5)

    def _set_tree_visible(self, visible: bool, persist: bool = True) -> None:
        self.query_one(NavPanel).set_class(not visible, "-hidden")
        self.query_one(TreeSplitter).set_class(not visible, "-hidden")
        if persist:
            config.save_setting("tree_visible", visible)

    def action_toggle_tree(self) -> None:
        """C-x d: show/hide the file-tree panel; the choice persists across
        runs, like the terminal but for the other side of the screen."""
        showing = self.query_one(NavPanel).has_class("-hidden")
        self._set_tree_visible(showing)
        if showing:
            self.query_one(FileTree).focus()
        elif (editor := self.active_editor) is not None:
            editor.focus()

    def action_grow_tree(self) -> None:
        """C-x }: widen the file tree (enlarge-window-horizontally)."""
        self._nudge_tree(4)

    def action_shrink_tree(self) -> None:
        """C-x {: narrow the file tree (shrink-window-horizontally)."""
        self._nudge_tree(-4)

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def _repl(self) -> TerminalPane:
        """The terminal, opened and with a shell running."""
        terminal = self.query_one(TerminalPane)
        if not terminal.has_class("-open"):
            terminal.add_class("-open")
        if not terminal.running:
            terminal.spawn()
        return terminal

    def action_send_to_repl(self) -> None:
        """C-c C-c: send the region (or current line) to the terminal, opening
        it if needed. Without a region the cursor advances one line, so
        repeated C-c C-c steps through a script."""
        editor = self.active_editor
        if editor is None:
            return
        terminal = self._repl()
        if editor.mark_active and not editor.selection.is_empty:
            text = editor.selected_text
            editor.deactivate_mark()
        else:
            row = editor.point[0]
            text = editor.document.get_line(row)
            if row + 1 < editor.document.line_count:
                editor.move_cursor((row + 1, 0))
        if not text.endswith("\n"):
            text += "\n"
        terminal.send_text(text)
        editor.focus()

    def action_send_code_block(self) -> None:
        """C-c C-b: send the fenced code block around point (its content,
        without the fences) to the terminal, then move past the closing
        fence so repeated C-c C-b steps block by block. Within a block,
        C-c C-c still sends a line or the region."""
        editor = self.active_editor
        if editor is None:
            return
        lines = editor.document.lines
        block = codeblocks.fence_at(lines, editor.point[0])
        if block is None:
            self.notify("Not in a fenced code block", severity="warning", timeout=2)
            return
        rows = block.content_rows(len(lines))
        if not rows:
            self.notify("Empty code block", timeout=2)
            return
        text = "\n".join(lines[rows.start : rows.stop]) + "\n"
        self._repl().send_text(text)
        editor.deactivate_mark()
        after = (block.close if block.close is not None else rows.stop - 1) + 1
        editor.move_cursor((min(after, editor.document.line_count - 1), 0))
        editor.focus()

    def action_query_replace(self) -> None:
        """M-%: interactive find/replace from point."""
        editor = self.active_editor
        if editor is None or not editor.writable():
            return

        def got_find(find: str | None) -> None:
            if not find:
                return

            def got_replacement(replacement: str | None) -> None:
                if replacement is None:
                    return
                self.push_screen(QueryReplaceScreen(editor, find, replacement))

            self.push_screen(
                PromptScreen(f"Query replace {find} with:"), got_replacement
            )

        self.push_screen(PromptScreen("Query replace:"), got_find)

    def action_project_search(self) -> None:
        """C-x g: regex search across the project tree."""

        async def got_pattern(pattern: str | None) -> None:
            if not pattern:
                return
            status = self.query_one(StatusBar)
            status.begin("search", f"Searching project for {pattern!r}…")
            try:
                results = await asyncio.to_thread(search_project, self._root, pattern)
            finally:
                status.end("search")
            if not results:
                self.notify(f"No matches for {pattern!r}", timeout=2)
                return

            async def picked(hit: tuple[Path, int] | None) -> None:
                if hit is None:
                    return
                path, line = hit
                await self._open_path(path)
                if (editor := self.active_editor) is not None:
                    editor.move_cursor((line - 1, 0), center=True)
                    editor.focus()

            self.push_screen(
                SearchResultsScreen(self._root, pattern, results), picked
            )

        self.push_screen(PromptScreen("Search project (regex):"), got_pattern)

    def action_toggle_terminal(self) -> None:
        terminal = self.query_one(TerminalPane)
        if terminal.has_class("-open"):
            terminal.remove_class("-open")
            if (editor := self.active_editor) is not None:
                editor.focus()
        else:
            terminal.add_class("-open")
            terminal.spawn()
            terminal.focus()

    def action_request_quit(self) -> None:
        # Dedupe linked views of the same modified buffer by path.
        seen: set = set()
        unsaved: list[str] = []
        for editor in self.all_editors():
            if not editor.modified:
                continue
            key = editor.path or id(editor)
            if key not in seen:
                seen.add(key)
                unsaved.append(editor.display_name)
        if not unsaved:
            self.exit()
            return

        def maybe_quit(confirmed: bool | None) -> None:
            if confirmed:
                self.exit()

        names = ", ".join(unsaved)
        self.push_screen(
            ConfirmScreen(f"Unsaved: {names}. Quit anyway?"), maybe_quit
        )

    # -- crash handling --------------------------------------------------------

    def _handle_exception(self, error: Exception) -> None:
        # Textual restores the terminal and stops the app on an unhandled
        # exception; persist the traceback and a snapshot of any unsaved work
        # so neither is lost with the TUI.
        try:
            self._crash_log = _write_crash_log(error)
        except Exception:
            pass
        try:
            recovery.snapshot(self._dirty_snapshots())
        except Exception:
            pass
        super()._handle_exception(error)


def _write_crash_log(error: BaseException) -> Path:
    import datetime
    import traceback

    directory = Path.home() / ".cache" / "candat"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    log = directory / f"crash-{stamp}.log"
    log.write_text(
        "".join(traceback.format_exception(type(error), error, error.__traceback__))
    )
    return log


def _enable_faulthandler():
    """Route hard crashes (SIGSEGV/SIGBUS/SIGABRT — e.g. a native tree-sitter
    fault) to a log file; the Python-level crash handler can't catch those.
    Returns the open file (kept for the process lifetime), or None."""
    import faulthandler

    directory = Path.home() / ".cache" / "candat"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        import datetime

        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        log = (directory / f"fault-{stamp}.log").open("w")
        faulthandler.enable(log)
    except OSError:
        return None
    return log


def main() -> None:
    fault_log = _enable_faulthandler()
    paths = [Path(arg) for arg in sys.argv[1:]]
    app = CandatApp(paths)
    app.run()
    if fault_log is not None:  # clean exit: drop the (empty) fault log
        name = fault_log.name
        fault_log.close()
        try:
            if Path(name).stat().st_size == 0:
                Path(name).unlink()
        except OSError:
            pass
    # Belt and braces: _handle_exception is a Textual internal, so if a future
    # Textual stops calling our override, recover the stored exception here.
    error = getattr(app, "_exception", None)
    if app._crash_log is None and error is not None:
        try:
            app._crash_log = _write_crash_log(error)
        except Exception:
            pass
    if app._crash_log is not None:
        print(
            f"\ncandat crashed — full traceback saved to {app._crash_log}",
            file=sys.stderr,
        )
        sys.exit(1)
