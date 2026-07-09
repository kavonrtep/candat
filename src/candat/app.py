"""Candat: a terminal IDE with emacs keybindings, built on Textual."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import DirectoryTree, Static, TabbedContent, TextArea

from .buffers import BufferListScreen
from .chords import CTRL_C_MAP, CTRL_X_MAP, ChordScreen
from .commands import CandatCommands
from .csvview import CSV_SUFFIXES
from .dialogs import ConfirmScreen, PromptScreen
from .editor import EditorBuffer
from .help import HelpScreen
from .killring import KillRing
from .nav import NavPanel
from .pane import BufferPane, pane_of
from .window import MAX_GROUPS, EditorGroup, group_of
from .preview import PREVIEW_MODES
from .projectsearch import SearchResultsScreen, search_project
from .replace import QueryReplaceScreen
from .terminal import TerminalPane
from .theme import CANDAT_LIGHT


class StatusBar(Static):
    """One-line status: buffer name, modified flag, cursor position, language."""

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
        self.update(
            f" {flag} {where}   Ln {row + 1}, Col {col + 1}   {language}{wrap}"
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
    NavPanel {
        border-right: solid $panel;
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
        self.kill_ring = KillRing()
        self.last_search = ""
        paths = paths or []
        self._root = Path.cwd()
        dirs = [p for p in paths if p.is_dir()]
        if dirs:
            self._root = dirs[0]
        self._files = [p for p in paths if not p.is_dir()]
        self._buffer_count = 0
        self._group_count = 1
        self._active_group: EditorGroup | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="workspace"):
            yield NavPanel(self._root)
            with Horizontal(id="groups"):
                yield EditorGroup(id="group-1")
        yield TerminalPane()
        yield StatusBar()

    async def on_mount(self) -> None:
        self.register_theme(CANDAT_LIGHT)
        self.theme = "candat-light"
        if self._files:
            for path in self._files:
                await self._open_path(path)
        else:
            await self._new_buffer()
        self.set_interval(1.0, self._check_disk_changes)

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
        if editor.language == "markdown":
            await pane.set_preview_mode("split")
        return pane

    async def _new_buffer(
        self, path: Path | None = None, load_text: bool = True
    ) -> EditorBuffer:
        editor = EditorBuffer(path=None)
        if path is not None and path.exists() and load_text:
            editor.load(path)
        else:
            editor.path = path
            editor._apply_language()
        await self._add_pane(editor)
        return editor

    async def _open_path(self, path: Path) -> None:
        path = path.expanduser().resolve()
        if path.is_dir():
            self.notify(f"{path} is a directory", severity="warning")
            return
        # CSV/TSV files open in the table viewer without loading the text.
        is_csv = path.suffix.lower() in CSV_SUFFIXES and path.exists()
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
            editor = current.editor
            if path.exists() and not is_csv:
                editor.load(path)
            else:
                editor.path = path
                editor._apply_language()
            self._refresh_tab_label(editor)
            self._refresh_status()
            if is_csv:
                current.enter_csv_mode(path)
                self._refresh_status()
                return
            if editor.language == "markdown":
                await current.set_preview_mode("split")
            editor.focus()
            return
        editor = await self._new_buffer(path, load_text=not is_csv)
        if is_csv and (pane := pane_of(editor)) is not None:
            pane.enter_csv_mode(path)
            self._refresh_status()
        if not path.exists():
            self.notify("(new file)", timeout=2)

    def _refresh_tab_label(self, editor: EditorBuffer) -> None:
        pane = pane_of(editor)
        group = group_of(editor)
        if pane is None or pane.id is None or group is None:
            return
        label = editor.display_name + ("*" if editor.modified else "")
        group.get_tab(pane.id).label = label

    def _refresh_status(self) -> None:
        editor = self.active_editor
        self.query_one(StatusBar).show(editor)
        self.sub_title = str(editor.path) if editor and editor.path else ""

    def _schedule_preview(self, editor: EditorBuffer) -> None:
        """Debounced live preview refresh while editing markdown."""
        pane = pane_of(editor)
        if pane is None or pane.preview_mode == "off":
            return
        if timer := getattr(editor, "_preview_timer", None):
            timer.stop()
        preview = pane.preview
        editor._preview_timer = self.set_timer(
            0.3, lambda: preview.render_text(editor.text)
        )

    def _toggle_csv_view(self, pane: BufferPane) -> None:
        """C-c C-v on a CSV buffer: switch between table view and text."""
        editor = pane.editor
        assert editor.path is not None
        if pane.is_csv:
            def to_text() -> None:
                if editor.disk_mtime is None and editor.path.exists():
                    editor.load(editor.path)
                    self._refresh_tab_label(editor)
                pane.leave_csv_mode()
                editor.focus()
                self._refresh_status()

            size = editor.path.stat().st_size if editor.path.exists() else 0
            if editor.disk_mtime is None and size > 5_000_000:
                def maybe(confirmed: bool | None) -> None:
                    if confirmed:
                        to_text()

                self.push_screen(
                    ConfirmScreen(
                        f"{editor.display_name} is {size / 1_000_000:.0f} MB; "
                        "load as text?"
                    ),
                    maybe,
                )
            else:
                to_text()
        else:
            try:
                mtime = editor.path.stat().st_mtime
            except OSError:
                mtime = None
            if pane.csv._path != editor.path or pane.csv.mtime != mtime:
                pane.enter_csv_mode(editor.path)
            else:
                pane.show_table()
            self._refresh_status()

    def action_toggle_preview(self) -> None:
        pane = self.active_pane
        if pane is None:
            return
        editor = pane.editor
        if editor.path is not None and editor.path.suffix.lower() in CSV_SUFFIXES:
            self._toggle_csv_view(pane)
            return
        if editor.language != "markdown":
            self.notify("Not a markdown buffer", severity="warning", timeout=2)
            return
        next_mode = PREVIEW_MODES[
            (PREVIEW_MODES.index(pane.preview_mode) + 1) % len(PREVIEW_MODES)
        ]
        self.call_later(pane.set_preview_mode, next_mode)

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

    def action_toggle_soft_wrap(self) -> None:
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
        editor.reload_from_disk()
        self._refresh_tab_label(editor)
        self._refresh_status()
        if editor.language == "markdown":
            self._schedule_preview(editor)
        self.notify(f"Reloaded {editor.display_name}", timeout=1.5)

    def action_isearch_forward(self) -> None:
        if (editor := self.active_editor) is not None:
            editor.action_isearch_forward()

    def action_isearch_backward(self) -> None:
        if (editor := self.active_editor) is not None:
            editor.action_isearch_backward()

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
        try:
            written = editor.save(path)
        except OSError as error:
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
        ring: list = [tree]
        for group in self.groups():
            if (pane := group.active_pane) is not None:
                ring.append(pane.visible_widget)
        if terminal.has_class("-open"):
            ring.append(terminal)
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

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_send_to_repl(self) -> None:
        """C-c C-c: send the region (or current line) to the terminal, opening
        it if needed. Without a region the cursor advances one line, so
        repeated C-c C-c steps through a script."""
        editor = self.active_editor
        if editor is None:
            return
        terminal = self.query_one(TerminalPane)
        if not terminal.has_class("-open"):
            terminal.add_class("-open")
        if not terminal.running:
            terminal.spawn()
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
            results = await asyncio.to_thread(search_project, self._root, pattern)
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


def main() -> None:
    paths = [Path(arg) for arg in sys.argv[1:]]
    CandatApp(paths).run()
