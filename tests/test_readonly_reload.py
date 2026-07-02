"""Tests for read-only mode and auto-reload of files changed on disk."""

import os
from pathlib import Path

import pytest
from textual.widgets import Input
from textual.widgets.text_area import Selection

from candat.app import CandatApp
from candat.dialogs import ConfirmScreen

pytestmark = pytest.mark.asyncio


async def chord(pilot, *keys):
    for key in keys:
        await pilot.press(key)
        await pilot.pause()
    await pilot.pause()


def touch_disk(path: Path, content: str) -> None:
    """Write new content with a guaranteed-different mtime."""
    old_mtime = path.stat().st_mtime if path.exists() else 0
    path.write_text(content)
    os.utime(path, (old_mtime + 5, old_mtime + 5))


# -- read-only mode ---------------------------------------------------------


async def test_toggle_read_only_blocks_typing_and_kills(tmp_path: Path):
    f = tmp_path / "a.py"
    f.write_text("original\n")
    app = CandatApp([f])
    async with app.run_test() as pilot:
        await pilot.pause()
        editor = app.active_editor
        await chord(pilot, "ctrl+x", "ctrl+q")
        assert editor.read_only
        await pilot.press("x")  # typing blocked by TextArea itself
        assert editor.text == "original\n"
        await pilot.press("ctrl+k")  # kill blocked by the writable() guard
        assert editor.text == "original\n"
        await pilot.press("ctrl+y")  # yank blocked too
        assert editor.text == "original\n"
        # Toggle back: editable again.
        await chord(pilot, "ctrl+x", "ctrl+q")
        assert not editor.read_only
        await pilot.press("x")
        assert editor.text == "xoriginal\n"


async def test_find_file_read_only(tmp_path: Path):
    f = tmp_path / "doc.txt"
    f.write_text("content\n")
    app = CandatApp()
    async with app.run_test() as pilot:
        await chord(pilot, "ctrl+x", "ctrl+r")
        prompt = app.screen.query_one(Input)
        prompt.value = str(f)
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        editor = app.active_editor
        assert editor.path == f
        assert editor.read_only


# -- disk watching ------------------------------------------------------------


async def test_clean_buffer_reloads_on_disk_change(tmp_path: Path):
    f = tmp_path / "watched.txt"
    f.write_text("line one\nline two\n")
    app = CandatApp([f])
    async with app.run_test() as pilot:
        await pilot.pause()
        editor = app.active_editor
        editor.selection = Selection((1, 3), (1, 3))
        touch_disk(f, "line one\nline two\nline three\n")
        app._check_disk_changes()
        await pilot.pause()
        assert editor.text == "line one\nline two\nline three\n"
        assert not editor.modified
        assert editor.point == (1, 3)  # cursor preserved


async def test_modified_buffer_asks_before_reload_yes(tmp_path: Path):
    f = tmp_path / "conflict.txt"
    f.write_text("disk v1\n")
    app = CandatApp([f])
    async with app.run_test() as pilot:
        await pilot.pause()
        editor = app.active_editor
        await pilot.press("e")  # local edit -> modified
        assert editor.modified
        touch_disk(f, "disk v2\n")
        app._check_disk_changes()
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.press("y")
        await pilot.pause()
        assert editor.text == "disk v2\n"
        assert not editor.modified


async def test_modified_buffer_asks_before_reload_no(tmp_path: Path):
    f = tmp_path / "keep.txt"
    f.write_text("disk v1\n")
    app = CandatApp([f])
    async with app.run_test() as pilot:
        await pilot.pause()
        editor = app.active_editor
        await pilot.press("e")
        touch_disk(f, "disk v2\n")
        app._check_disk_changes()
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.press("n")
        await pilot.pause()
        assert editor.text == "edisk v1\n"  # edits kept
        assert editor.modified
        # Same disk state: no re-prompt on the next poll.
        app._check_disk_changes()
        await pilot.pause()
        assert not isinstance(app.screen, ConfirmScreen)
        # A further disk change asks again.
        touch_disk(f, "disk v3\n")
        app._check_disk_changes()
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.press("ctrl+g")


async def test_reload_after_own_save_does_not_trigger(tmp_path: Path):
    f = tmp_path / "self.txt"
    f.write_text("v1\n")
    app = CandatApp([f])
    async with app.run_test() as pilot:
        await pilot.pause()
        editor = app.active_editor
        await pilot.press("x")
        await chord(pilot, "ctrl+x", "ctrl+s")  # save own edit
        assert not editor.modified
        app._check_disk_changes()
        await pilot.pause()
        # Our own save must not be treated as an external change.
        assert editor.text == "xv1\n"
        assert len(app.screen_stack) == 1
