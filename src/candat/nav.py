"""The navigation panel: a file tree with a live filter box on top.

Typing in the filter narrows the tree to files whose path (relative to the
root) contains the query, keeping their ancestor directories so matches stay
reachable, and auto-expanding so they are visible. Clearing it restores the
full lazy tree.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widget import Widget
from textual.widgets import DirectoryTree, Input

from . import config

IGNORE_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    "node_modules",
    ".ruff_cache",
}
MAX_MATCHES = 5000

# Folder / open-folder / file glyphs for the tree. Emoji is the default but
# render poorly in some terminals (e.g. Konsole); nerd needs a Nerd Font as
# the terminal font; ascii always works. Pick with $CANDAT_TREE_ICONS or the
# `cycle-tree-icons` command.
TREE_ICON_SETS: dict[str, tuple[str, str, str]] = {
    "emoji": ("📁 ", "📂 ", "📄 "),
    "nerd": (" ", " ", " "),  # nf-fa folder / folder-open / file
    "ascii": ("▸ ", "▾ ", "· "),
}
DEFAULT_ICONS = "emoji"


def resolve_icon_set(name: str | None) -> str:
    if name is None:
        name = os.environ.get("CANDAT_TREE_ICONS")
    if name is None:
        name = str(config.load()["tree_icons"])
    return name if name in TREE_ICON_SETS else DEFAULT_ICONS


class FileTree(DirectoryTree):
    """A DirectoryTree that can filter to files matching a substring query."""

    BINDINGS = [Binding("slash", "focus_filter", "filter", show=False)]

    def __init__(self, path, icons: str | None = None, **kwargs) -> None:
        super().__init__(path, **kwargs)
        self._query = ""
        self._allowed: set[Path] = set()
        self._icon_set = DEFAULT_ICONS
        self.apply_icons(resolve_icon_set(icons))

    def apply_icons(self, name: str) -> None:
        self._icon_set = name
        self.ICON_NODE, self.ICON_NODE_EXPANDED, self.ICON_FILE = TREE_ICON_SETS[name]

    def cycle_icons(self) -> str:
        """Switch to the next icon set live and re-render; returns its name.
        The choice is persisted to the config file, so it sticks across runs."""
        order = list(TREE_ICON_SETS)
        nxt = order[(order.index(self._icon_set) + 1) % len(order)]
        self.apply_icons(nxt)
        config.save_setting("tree_icons", nxt)
        # refresh() alone leaves the cached node labels (icons only update when
        # the tree next re-renders, e.g. on focus); _invalidate (a Tree
        # internal) clears the line cache so the new icons show immediately —
        # degrade to a plain refresh if a future Textual drops it.
        invalidate = getattr(self, "_invalidate", None)
        if invalidate is not None:
            invalidate()
        else:
            self.refresh(layout=True)
        return nxt

    def filter_paths(self, paths: Iterable[Path]) -> Iterable[Path]:
        if not self._query:
            return paths
        return [p for p in paths if p in self._allowed]

    async def set_filter(self, query: str) -> None:
        """Apply a filter, reload, and reveal the matches."""
        self._query = query.strip().lower()
        if not self._query:
            self._allowed = set()
            await self.reload()
            return
        self._allowed = self._matching()
        await self._reveal_matches()

    async def _reveal_matches(self) -> None:
        """Load and expand exactly the directories on the path to a match.

        DirectoryTree loads children lazily, so expand_all() cannot reach a
        deep match in one pass. Walk the allowed directories top-down,
        loading each node's (already filtered) children before descending.
        """
        stack = [self.root]
        while stack:
            node = stack.pop()
            await self.reload_node(node)
            node.expand()
            for child in node.children:
                entry = child.data
                if entry is not None and entry.path in self._allowed and entry.path.is_dir():
                    stack.append(child)

    def _matching(self) -> set[Path]:
        root = Path(os.path.abspath(self.path))
        allowed: set[Path] = set()
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS]
            base = Path(dirpath)
            for name in filenames:
                path = base / name
                if self._query in os.path.relpath(path, root).lower():
                    node = path
                    while node not in allowed:
                        allowed.add(node)
                        if node == root:
                            break
                        node = node.parent
            if len(allowed) > MAX_MATCHES:
                break
        return allowed

    def action_focus_filter(self) -> None:
        if isinstance(self.parent, NavPanel):
            self.parent.query_one(Input).focus()


MIN_TREE_WIDTH = 16
DEFAULT_TREE_WIDTH = 32


class TreeSplitter(Widget):
    """A one-cell grab column between the file tree and the editors: drag to
    resize the tree, double-click to reset its width."""

    DEFAULT_CSS = """
    TreeSplitter {
        width: 1;
        color: $panel;
    }
    TreeSplitter:hover {
        color: $primary;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._dragging = False

    def render(self):
        # A │ on every row — without this, Textual's default rendering would
        # spill the widget's name down the 1-cell column, one letter per line.
        from rich.text import Text

        return Text("\n".join(["│"] * max(1, self.size.height)), end="")

    def on_mouse_down(self, event: events.MouseDown) -> None:
        event.stop()
        self._dragging = True
        self.capture_mouse()

    def on_mouse_move(self, event: events.MouseMove) -> None:
        if not self._dragging:
            return
        # The tree starts at x=0, so the pointer's screen column IS the width.
        self.app.set_tree_width(event.screen_x, persist=False)

    def on_mouse_up(self, event: events.MouseUp) -> None:
        if not self._dragging:
            return
        event.stop()
        self._dragging = False
        self.capture_mouse(False)
        self.app.set_tree_width(event.screen_x, persist=True)

    def on_click(self, event: events.Click) -> None:
        if event.chain == 2:  # double click: back to the default width
            event.stop()
            self.app.set_tree_width(DEFAULT_TREE_WIDTH, persist=True)


class NavPanel(Vertical):
    """File tree plus its filter input."""

    DEFAULT_CSS = """
    NavPanel {
        width: 32;
    }
    NavPanel Input#tree-filter {
        height: 1;
        border: none;
        padding: 0 1;
        background: $surface;
    }
    NavPanel FileTree {
        height: 1fr;
        background: $surface;
    }
    """

    def __init__(self, root: Path, **kwargs) -> None:
        super().__init__(**kwargs)
        self._root = root
        self._debounce = None

    def on_mount(self) -> None:
        width = config.load()["tree_width"]
        if isinstance(width, int) and width >= MIN_TREE_WIDTH:
            self.styles.width = width

    def compose(self) -> ComposeResult:
        yield Input(placeholder="Filter files  (/)", id="tree-filter")
        yield FileTree(self._root, id="tree")

    @property
    def tree(self) -> FileTree:
        return self.query_one(FileTree)

    @on(Input.Changed, "#tree-filter")
    def _filter_changed(self, event: Input.Changed) -> None:
        if self._debounce is not None:
            self._debounce.stop()
        query = event.value
        self._debounce = self.set_timer(
            0.15, lambda: self.run_worker(self.tree.set_filter(query))
        )

    @on(Input.Submitted, "#tree-filter")
    def _filter_submitted(self) -> None:
        # Enter jumps into the (filtered) tree to navigate results.
        self.tree.focus()

    def on_key(self, event) -> None:
        # Esc in the filter clears it and returns to the tree.
        if event.key == "escape" and isinstance(self.focused_child, Input):
            event.stop()
            filter_input = self.query_one(Input)
            filter_input.value = ""
            self.run_worker(self.tree.set_filter(""))
            self.tree.focus()

    @property
    def focused_child(self):
        return self.app.focused
