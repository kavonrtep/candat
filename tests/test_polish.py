"""Tests for roadmap polish: R highlighting, path completion, buffer list,
terminal scrollback."""

import asyncio
from pathlib import Path

import pytest
from textual.widgets import Input

from candat.app import CandatApp
from candat.buffers import BufferListScreen
from candat.terminal import TerminalPane
from helpers import chord

pytestmark = pytest.mark.asyncio


async def test_r_files_get_highlighting(tmp_path: Path):
    script = tmp_path / "analysis.R"
    script.write_text('x <- c(1, 2)\nif (TRUE) print("ok")\n')
    app = CandatApp([script])
    async with app.run_test() as pilot:
        await pilot.pause()
        editor = app.active_editor
        assert editor.language == "r"
        assert "r" in editor.available_languages


async def test_find_file_tab_completion(tmp_path: Path):
    (tmp_path / "unique_name.txt").write_text("x")
    (tmp_path / "subdir").mkdir()
    app = CandatApp()
    async with app.run_test() as pilot:
        await chord(pilot, "ctrl+x", "ctrl+f")
        prompt = app.screen.query_one(Input)
        prompt.value = str(tmp_path / "uni")
        prompt.cursor_position = len(prompt.value)
        await pilot.press("tab")
        await pilot.pause()
        assert prompt.value == str(tmp_path / "unique_name.txt")
        # Directories complete with a trailing slash.
        prompt.value = str(tmp_path / "sub")
        prompt.cursor_position = len(prompt.value)
        await pilot.press("tab")
        await pilot.pause()
        assert prompt.value == str(tmp_path / "subdir") + "/"
        await pilot.press("escape")


async def test_buffer_list_switches_buffers(tmp_path: Path):
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("aaa")
    b.write_text("bbb")
    app = CandatApp([a, b])
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.active_editor.path == b  # last opened is active
        await chord(pilot, "ctrl+x", "b")
        assert isinstance(app.screen, BufferListScreen)
        # The other (next) buffer is preselected: Enter switches to a.txt.
        await pilot.press("enter")
        await pilot.pause()
        assert app.active_editor.path == a
        # C-g cancels without switching.
        await chord(pilot, "ctrl+x", "b")
        await pilot.press("ctrl+g")
        await pilot.pause()
        assert app.active_editor.path == a


async def test_terminal_scrollback(bash_shell):
    app = CandatApp()
    async with app.run_test() as pilot:
        await chord(pilot, "ctrl+x", "t")
        terminal = app.query_one(TerminalPane)
        for ch in "seq 1 100":
            await pilot.press(ch)
        await pilot.press("enter")
        deadline = 50
        while deadline and "100" not in "\n".join(terminal._screen.display):
            await asyncio.sleep(0.1)
            await pilot.pause()
            deadline -= 1
        # Let the prompt repaint finish: any new output snaps history to bottom.
        await asyncio.sleep(0.5)
        await pilot.pause()
        assert not terminal.scrolled_back
        await pilot.press("shift+pageup")
        assert terminal.scrolled_back
        assert terminal.border_title  # history indicator shown
        # Typing snaps back to the live view.
        await pilot.press("x")
        assert not terminal.scrolled_back
