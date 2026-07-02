"""Pike: a terminal IDE with emacs keybindings, built on Textual."""

from __future__ import annotations

import sys
from pathlib import Path

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import (
    DirectoryTree,
    Static,
    TabbedContent,
    TabPane,
    Tabs,
    TextArea,
)

from .chords import CTRL_C_MAP, CTRL_X_MAP, ChordScreen
from .commands import PikeCommands
from .dialogs import ConfirmScreen, PromptScreen
from .editor import EditorBuffer
from .killring import KillRing
from .preview import PREVIEW_CLASSES, PREVIEW_MODES, MarkdownPreview
from .terminal import TerminalPane
from .theme import PIKE_LIGHT


class StatusBar(Static):
    """One-line status: buffer name, modified flag, cursor position, language."""

    def show(self, editor: EditorBuffer | None) -> None:
        if editor is None:
            self.update(" pike")
            return
        modified = "*" if editor.modified else ""
        row, col = editor.cursor_location
        language = editor.language or "text"
        where = str(editor.path) if editor.path else editor.display_name
        self.update(f" {where}{modified}   Ln {row + 1}, Col {col + 1}   {language}")


class PikeApp(App[None]):
    TITLE = "pike"
    COMMAND_PALETTE_BINDING = "ctrl+shift+p"
    COMMANDS = App.COMMANDS | {PikeCommands}

    CSS = """
    #workspace {
        height: 1fr;
    }
    DirectoryTree {
        width: 32;
        max-width: 40%;
        border-right: solid $panel;
        background: $surface;
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
        Binding("alt+x", "command_palette", "M-x", show=False),
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

    def compose(self) -> ComposeResult:
        with Horizontal(id="workspace"):
            yield DirectoryTree(self._root)
            yield TabbedContent()
        yield TerminalPane()
        yield StatusBar()

    async def on_mount(self) -> None:
        self.register_theme(PIKE_LIGHT)
        self.theme = "pike-light"
        if self._files:
            for path in self._files:
                await self._open_path(path)
        else:
            await self._new_buffer()

    # -- buffer bookkeeping ------------------------------------------------

    @property
    def tabs(self) -> TabbedContent:
        return self.query_one(TabbedContent)

    @property
    def active_editor(self) -> EditorBuffer | None:
        pane = self.tabs.active_pane
        if pane is None:
            return None
        return pane.query_one(EditorBuffer)

    def editors(self) -> list[EditorBuffer]:
        return list(self.query(EditorBuffer))

    async def _new_buffer(self, path: Path | None = None) -> EditorBuffer:
        self._buffer_count += 1
        pane_id = f"buffer-{self._buffer_count}"
        editor = EditorBuffer(path=None)
        if path is not None and path.exists():
            editor.load(path)
        else:
            editor.path = path
            editor._apply_language()
        pane = TabPane(
            editor.display_name, Horizontal(editor, MarkdownPreview()), id=pane_id
        )
        await self.tabs.add_pane(pane)
        self.tabs.active = pane_id
        editor.focus()
        if editor.language == "markdown":
            await self._set_preview_mode(pane, "split")
        return editor

    async def _open_path(self, path: Path) -> None:
        path = path.expanduser().resolve()
        if path.is_dir():
            self.notify(f"{path} is a directory", severity="warning")
            return
        # Already open? Just switch to it.
        for editor in self.editors():
            if editor.path == path:
                pane = self._pane_of(editor)
                if pane is not None and pane.id is not None:
                    self.tabs.active = pane.id
                editor.focus()
                return
        # Reuse a pristine untitled buffer instead of stacking new tabs.
        current = self.active_editor
        if current and current.path is None and not current.modified and not current.text:
            if path.exists():
                current.load(path)
            else:
                current.path = path
                current._apply_language()
            self._refresh_tab_label(current)
            current.focus()
            self._refresh_status()
            pane = self._pane_of(current)
            if current.language == "markdown" and pane is not None:
                await self._set_preview_mode(pane, "split")
            return
        await self._new_buffer(path)
        if not path.exists():
            self.notify("(new file)", timeout=2)

    def _pane_of(self, editor: EditorBuffer) -> TabPane | None:
        for ancestor in editor.ancestors:
            if isinstance(ancestor, TabPane):
                return ancestor
        return None

    def _refresh_tab_label(self, editor: EditorBuffer) -> None:
        pane = self._pane_of(editor)
        if pane is None or pane.id is None:
            return
        label = editor.display_name + ("*" if editor.modified else "")
        self.tabs.get_tab(pane.id).label = label

    def _refresh_status(self) -> None:
        self.query_one(StatusBar).show(self.active_editor)
        editor = self.active_editor
        self.sub_title = str(editor.path) if editor and editor.path else ""

    # -- markdown preview ----------------------------------------------------

    def _preview_mode(self, pane: TabPane) -> str:
        if pane.has_class("-preview-only"):
            return "only"
        if pane.has_class("-preview-split"):
            return "split"
        return "off"

    async def _set_preview_mode(self, pane: TabPane, mode: str) -> None:
        pane.remove_class(*PREVIEW_CLASSES.values())
        if mode in PREVIEW_CLASSES:
            pane.add_class(PREVIEW_CLASSES[mode])
        editor = pane.query_one(EditorBuffer)
        preview = pane.query_one(MarkdownPreview)
        if mode != "off":
            await preview.render_text(editor.text)
        if mode == "only":
            preview.focus()
        else:
            editor.focus()

    def _schedule_preview(self, editor: EditorBuffer) -> None:
        """Debounced live preview refresh while editing markdown."""
        pane = self._pane_of(editor)
        if pane is None or self._preview_mode(pane) == "off":
            return
        if timer := getattr(editor, "_preview_timer", None):
            timer.stop()
        preview = pane.query_one(MarkdownPreview)
        editor._preview_timer = self.set_timer(
            0.3, lambda: preview.render_text(editor.text)
        )

    def action_toggle_preview(self) -> None:
        editor = self.active_editor
        pane = self._pane_of(editor) if editor else None
        if editor is None or pane is None:
            return
        if editor.language != "markdown":
            self.notify("Not a markdown buffer", severity="warning", timeout=2)
            return
        current = self._preview_mode(pane)
        next_mode = PREVIEW_MODES[
            (PREVIEW_MODES.index(current) + 1) % len(PREVIEW_MODES)
        ]
        self.call_later(self._set_preview_mode, pane, next_mode)

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
        if (editor := self.active_editor) is not None:
            editor.undo()

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

        self.push_screen(PromptScreen("Find file:", initial), opened)

    def action_save_buffer(self) -> None:
        editor = self.active_editor
        if editor is None:
            return
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

        self.push_screen(PromptScreen("Write file:", initial), written)

    def _save(self, editor: EditorBuffer, path: Path | None) -> None:
        try:
            written = editor.save(path)
        except OSError as error:
            self.notify(f"Save failed: {error}", severity="error")
            return
        self._refresh_tab_label(editor)
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
        pane = self._pane_of(editor)
        if pane is not None and pane.id is not None:
            await self.tabs.remove_pane(pane.id)
        if not self.editors():
            await self._new_buffer()
        self._refresh_status()

    def action_switch_buffer(self) -> None:
        """Cycle to the next buffer (buffer list comes later)."""
        self.tabs.query_one(Tabs).action_next_tab()
        editor = self.active_editor
        if editor is not None:
            editor.focus()

    def action_other_window(self) -> None:
        """C-x o: cycle focus tree -> editor -> terminal (when open)."""
        editor = self.active_editor
        tree = self.query_one(DirectoryTree)
        terminal = self.query_one(TerminalPane)
        ring: list = [tree]
        if editor is not None:
            ring.append(editor)
        if terminal.has_class("-open"):
            ring.append(terminal)
        focused = self.focused
        for index, widget in enumerate(ring):
            if focused is widget or (focused is not None and widget in focused.ancestors_with_self):
                ring[(index + 1) % len(ring)].focus()
                return
        ring[0].focus()

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
        unsaved = [e.display_name for e in self.editors() if e.modified]
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
    PikeApp(paths).run()
