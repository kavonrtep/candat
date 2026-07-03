"""Pure helper functions shared across the test-suite.

Kept out of conftest.py so tests import them explicitly:
    from helpers import chord, wait_for
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from textual.widgets.text_area import Selection

from candat.app import CandatApp


@asynccontextmanager
async def open_app(paths: list[Path] | None = None, size: tuple[int, int] = (120, 40)):
    """Run a CandatApp for a test, yielding (app, pilot).

        async with open_app([path]) as (app, pilot):
            ...

    Enter and exit stay in the test's own async context, which Textual's
    run_test() requires — a factory fixture that exits in teardown trips a
    context-var error.
    """
    app = CandatApp(paths)
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        yield app, pilot


@asynccontextmanager
async def editor_with_text(text: str = "", path: Path | None = None):
    """Run an app whose active buffer holds `text` (cursor at the top), or
    opens `path` if given. Yields (app, pilot, editor)."""
    async with open_app([path] if path else None) as (app, pilot):
        editor = app.active_editor
        if path is None:
            editor.text = text
            editor.selection = Selection((0, 0), (0, 0))
        await pilot.pause()
        yield app, pilot, editor


async def chord(pilot, *keys) -> None:
    """Press a key sequence one key at a time (letting each screen mount),
    then flush the deferred chord dispatch."""
    for key in keys:
        await pilot.press(key)
        await pilot.pause()
    await pilot.pause()


async def wait_for(pilot, predicate, timeout: float = 8.0) -> bool:
    """Pump the app until predicate() is true or timeout elapses."""
    elapsed = 0.0
    while elapsed < timeout:
        await pilot.pause()
        if predicate():
            return True
        await asyncio.sleep(0.1)
        elapsed += 0.1
    return False


def write_csv(path: Path, rows: int) -> None:
    """Write a CSV with an id,name,value header and `rows` data rows."""
    lines = ["id,name,value"]
    lines += [f"{i},item{i},{i * 10}" for i in range(1, rows + 1)]
    path.write_text("\n".join(lines) + "\n")


def touch_disk(path: Path, content: str) -> None:
    """Write new content with a guaranteed-later mtime, so the disk watcher
    reliably notices the change regardless of filesystem mtime resolution."""
    old_mtime = path.stat().st_mtime if path.exists() else 0
    path.write_text(content)
    os.utime(path, (old_mtime + 5, old_mtime + 5))


def terminal_text(terminal) -> str:
    """The visible text of a TerminalPane's pyte screen, joined by newlines."""
    return "\n".join(terminal._screen.display) if terminal._screen else ""


def make_project(tmp_path: Path) -> Path:
    """A small tree with two matchable files and a .git dir that must be
    excluded from project search."""
    (tmp_path / "alpha.py").write_text("def needle_one():\n    pass\n")
    sub = tmp_path / "pkg"
    sub.mkdir()
    (sub / "beta.py").write_text("x = 1\nneedle_two = 2\n")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "junk.py").write_text("needle_hidden\n")
    return tmp_path
