"""Tests for editor-group window splitting (C-x 2 / 3 / 0 / 1 / o)."""

from pathlib import Path

import pytest

from candat.window import EditorGroup
from helpers import chord, open_app

pytestmark = pytest.mark.asyncio


def group_buffers(app):
    return [g.active_pane.editor.display_name for g in app.groups()]


async def test_split_right_creates_second_window(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("a\n")
    async with open_app([f]) as (app, pilot):
        assert len(app.groups()) == 1
        await chord(pilot, "ctrl+x", "3")
        assert len(app.groups()) == 2
        assert not app.query_one("#groups").has_class("-stacked")
        # The new window is active and holds a fresh scratch buffer.
        assert app.active_editor.display_name == "*untitled*"
        assert app.query_one("#groups").has_class("-split")


async def test_split_below_is_stacked(tmp_path):
    async with open_app() as (app, pilot):
        await chord(pilot, "ctrl+x", "2")
        assert len(app.groups()) == 2
        assert app.query_one("#groups").has_class("-stacked")


async def test_open_file_goes_to_active_window(tmp_path):
    a = tmp_path / "a.py"
    b = tmp_path / "b.py"
    a.write_text("a\n")
    b.write_text("b\n")
    async with open_app([a]) as (app, pilot):
        await chord(pilot, "ctrl+x", "3")  # new active window
        await app._open_path(b)
        await pilot.pause()
        # Two windows, each showing its own file.
        assert sorted(group_buffers(app)) == ["a.py", "b.py"]
        assert app.active_editor.path == b


async def test_other_window_cycles_between_windows(tmp_path):
    a = tmp_path / "a.py"
    b = tmp_path / "b.py"
    a.write_text("a\n")
    b.write_text("b\n")
    async with open_app([a]) as (app, pilot):
        await chord(pilot, "ctrl+x", "3")
        await app._open_path(b)
        await pilot.pause()
        first = app.active_editor
        # Focus the first window's editor, then C-x o should reach the other.
        app.groups()[0].active_pane.editor.focus()
        await pilot.pause()
        seen = set()
        for _ in range(5):
            await chord(pilot, "ctrl+x", "o")
            ed = app.active_editor
            if ed is not None:
                seen.add(ed.path)
        assert {a, b} <= seen


async def test_delete_window(tmp_path):
    async with open_app() as (app, pilot):
        await chord(pilot, "ctrl+x", "3")
        assert len(app.groups()) == 2
        await chord(pilot, "ctrl+x", "0")  # scratch is unmodified -> no confirm
        assert len(app.groups()) == 1
        assert not app.query_one("#groups").has_class("-split")


async def test_delete_sole_window_is_noop(tmp_path):
    async with open_app() as (app, pilot):
        await chord(pilot, "ctrl+x", "0")
        assert len(app.groups()) == 1


async def test_delete_other_windows(tmp_path):
    async with open_app() as (app, pilot):
        await chord(pilot, "ctrl+x", "3")
        await chord(pilot, "ctrl+x", "3")
        assert len(app.groups()) == 3
        await chord(pilot, "ctrl+x", "1")
        assert len(app.groups()) == 1


async def test_delete_window_confirms_when_unsaved(tmp_path):
    async with open_app() as (app, pilot):
        await chord(pilot, "ctrl+x", "3")
        await pilot.press("x")  # dirty the new window's scratch buffer
        await pilot.pause()
        await chord(pilot, "ctrl+x", "0")
        # A confirm screen is up; declining keeps the window.
        from candat.dialogs import ConfirmScreen

        assert isinstance(app.screen, ConfirmScreen)
        await pilot.press("n")
        await pilot.pause()
        assert len(app.groups()) == 2


async def test_max_windows(tmp_path):
    async with open_app() as (app, pilot):
        for _ in range(6):
            await chord(pilot, "ctrl+x", "3")
        from candat.window import MAX_GROUPS

        assert len(app.groups()) == MAX_GROUPS


async def test_disk_watch_reloads_across_windows(tmp_path):
    a = tmp_path / "a.txt"
    a.write_text("v1\n")
    async with open_app([a]) as (app, pilot):
        await chord(pilot, "ctrl+x", "3")  # focus a second window
        # a.txt lives in the (now non-active) first window; change it on disk.
        import os

        old = a.stat().st_mtime
        a.write_text("v2\n")
        os.utime(a, (old + 5, old + 5))
        app._check_disk_changes()
        await pilot.pause()
        editor = app.groups()[0].active_pane.editor
        assert editor.text == "v2\n"
