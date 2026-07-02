"""End-to-end smoke tests driving PikeApp with Textual's test pilot."""

from pathlib import Path

import pytest
from textual.widgets import DirectoryTree, Input, TabbedContent

from pike.app import PikeApp, StatusBar
from pike.editor import EditorBuffer

pytestmark = pytest.mark.asyncio


async def chord(pilot, *keys):
    """Press a key sequence one key at a time (letting each screen mount),
    then flush the deferred chord dispatch."""
    for key in keys:
        await pilot.press(key)
        await pilot.pause()
    await pilot.pause()


async def test_boot_layout_and_theme():
    app = PikeApp()
    async with app.run_test() as pilot:
        assert app.theme == "pike-light"
        app.query_one(DirectoryTree)
        app.query_one(TabbedContent)
        app.query_one(StatusBar)
        editor = app.active_editor
        assert editor is not None
        assert editor.path is None
        assert not editor.modified


async def test_find_file_edit_and_save(tmp_path: Path):
    target = tmp_path / "hello.py"
    target.write_text("print('hi')\n")
    app = PikeApp()
    async with app.run_test() as pilot:
        # C-x C-f, type the path, enter.
        await chord(pilot, "ctrl+x", "ctrl+f")
        prompt = app.screen.query_one(Input)
        prompt.value = str(target)
        await pilot.press("enter")
        await pilot.pause()

        editor = app.active_editor
        assert editor is not None
        assert editor.path == target
        assert editor.text == "print('hi')\n"
        assert editor.language == "python"
        assert not editor.modified

        # Type at the top of the buffer -> modified, tab label gets a star.
        await pilot.press("#")
        assert editor.modified
        pane = app.tabs.active_pane
        assert str(app.tabs.get_tab(pane.id).label) == "hello.py*"

        # C-x C-s writes it back out.
        await chord(pilot, "ctrl+x", "ctrl+s")
        assert not editor.modified
        assert target.read_text().startswith("#print")


async def test_save_untitled_prompts_for_path(tmp_path: Path):
    app = PikeApp()
    async with app.run_test() as pilot:
        await pilot.press("h", "i")
        await chord(pilot, "ctrl+x", "ctrl+s")
        prompt = app.screen.query_one(Input)
        prompt.value = str(tmp_path / "note.md")
        await pilot.press("enter")
        await pilot.pause()
        assert (tmp_path / "note.md").read_text() == "hi"
        editor = app.active_editor
        assert editor.language == "markdown"
        assert not editor.modified


async def test_undefined_chord_and_cancel():
    app = PikeApp()
    async with app.run_test() as pilot:
        # C-g cancels a pending chord.
        await chord(pilot, "ctrl+x", "ctrl+g")
        assert len(app.screen_stack) == 1
        # Undefined chord key just warns; app stays up.
        await chord(pilot, "ctrl+x", "z")
        assert len(app.screen_stack) == 1


async def test_kill_buffer_and_quit_confirmation(tmp_path: Path):
    app = PikeApp()
    async with app.run_test() as pilot:
        await pilot.press("x")  # dirty the untitled buffer
        # C-x C-c should ask about unsaved changes; answer no.
        await chord(pilot, "ctrl+x", "ctrl+c")
        assert len(app.screen_stack) == 2
        await pilot.press("n")
        await pilot.pause()
        assert not app._exit  # still running

        # Kill the modified buffer (confirm yes) -> replaced by fresh untitled.
        await chord(pilot, "ctrl+x", "k")
        await pilot.press("y")
        await pilot.pause()
        editor = app.active_editor
        assert editor is not None
        assert editor.text == ""
        assert not editor.modified

        # Clean app quits without asking.
        await chord(pilot, "ctrl+x", "ctrl+c")
        assert app._exit
