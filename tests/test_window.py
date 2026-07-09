"""Tests for editor-group window splitting (C-x 2 / 3 / 0 / 1 / o)."""

from pathlib import Path

import pytest

from candat.window import EditorGroup
from helpers import chord, open_app

pytestmark = pytest.mark.asyncio


def group_buffers(app):
    return [g.active_pane.editor.display_name for g in app.groups()]


async def test_split_right_shows_same_buffer_linked(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("a\n")
    async with open_app([f]) as (app, pilot):
        assert len(app.groups()) == 1
        await chord(pilot, "ctrl+x", "3")
        assert len(app.groups()) == 2
        assert not app.query_one("#groups").has_class("-stacked")
        # Emacs: the new window shows the SAME buffer, as a linked view.
        assert app.active_editor.path == f
        v1 = app.groups()[0].active_pane.editor
        v2 = app.groups()[1].active_pane.editor
        assert v1 is not v2
        assert v2 in v1.links and v1 in v2.links
        assert app.query_one("#groups").has_class("-split")


async def test_linked_views_share_edits_keep_own_cursor(tmp_path):
    from textual.widgets.text_area import Selection

    f = tmp_path / "notes.txt"
    f.write_text("l0\nl1\nl2\nl3\n")
    async with open_app([f]) as (app, pilot):
        v1 = app.active_editor
        v1.selection = Selection((3, 0), (3, 0))
        await chord(pilot, "ctrl+x", "3")
        v2 = app.active_editor
        v2.selection = Selection((0, 0), (0, 0))
        v2.insert("NEW\n", (0, 0))
        await pilot.pause()
        assert v1.text == v2.text == "NEW\nl0\nl1\nl2\nl3\n"
        assert v1.selection.end == (4, 0)  # shifted down by the inserted line
        assert v2.selection.end == (1, 0)
        assert v1.modified and v2.modified


async def test_saving_one_linked_view_clears_both(tmp_path):
    f = tmp_path / "notes.txt"
    f.write_text("x\n")
    async with open_app([f]) as (app, pilot):
        v1 = app.active_editor
        await chord(pilot, "ctrl+x", "3")
        v2 = app.active_editor
        v2.insert("y", (0, 0))
        await pilot.pause()
        assert v1.modified and v2.modified
        await chord(pilot, "ctrl+x", "ctrl+s")
        assert not v1.modified and not v2.modified
        assert f.read_text() == "yx\n"


async def test_closing_one_window_keeps_buffer_in_other(tmp_path):
    f = tmp_path / "notes.txt"
    f.write_text("keep\n")
    async with open_app([f]) as (app, pilot):
        await chord(pilot, "ctrl+x", "3")
        await chord(pilot, "ctrl+x", "0")  # close the linked window
        assert len(app.groups()) == 1
        editor = app.active_editor
        assert editor.path == f
        assert editor.text == "keep\n"
        assert editor.links == []  # detached, edits no longer forwarded


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
