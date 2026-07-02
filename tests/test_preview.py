"""Tests for the live markdown preview."""

import asyncio
from pathlib import Path

import pytest
from textual.widgets import Markdown

from candat.app import CandatApp
from candat.preview import MarkdownPreview

pytestmark = pytest.mark.asyncio


async def chord(pilot, *keys):
    for key in keys:
        await pilot.press(key)
        await pilot.pause()
    await pilot.pause()


async def test_markdown_opens_in_split_preview(tmp_path: Path):
    note = tmp_path / "note.md"
    note.write_text("# Title\n\nSome text.\n")
    app = CandatApp([note])
    async with app.run_test() as pilot:
        await pilot.pause()
        pane = app.tabs.active_pane
        assert app._preview_mode(pane) == "split"
        preview = pane.query_one(MarkdownPreview)
        assert preview.styles.display != "none"
        # Rendered document contains the heading text.
        assert pane.query_one(Markdown).source.startswith("# Title")


async def test_non_markdown_has_no_preview(tmp_path: Path):
    code = tmp_path / "x.py"
    code.write_text("print(1)\n")
    app = CandatApp([code])
    async with app.run_test() as pilot:
        await pilot.pause()
        pane = app.tabs.active_pane
        assert app._preview_mode(pane) == "off"
        # C-c C-v refuses politely.
        await chord(pilot, "ctrl+c", "ctrl+v")
        assert app._preview_mode(pane) == "off"


async def test_toggle_cycles_modes(tmp_path: Path):
    note = tmp_path / "note.md"
    note.write_text("hello\n")
    app = CandatApp([note])
    async with app.run_test() as pilot:
        await pilot.pause()
        pane = app.tabs.active_pane
        assert app._preview_mode(pane) == "split"
        await chord(pilot, "ctrl+c", "ctrl+v")
        assert app._preview_mode(pane) == "only"
        assert app.focused is pane.query_one(MarkdownPreview)
        await chord(pilot, "ctrl+c", "ctrl+v")
        assert app._preview_mode(pane) == "off"
        assert app.focused is app.active_editor
        await chord(pilot, "ctrl+c", "ctrl+v")
        assert app._preview_mode(pane) == "split"


async def test_preview_updates_after_debounce(tmp_path: Path):
    note = tmp_path / "note.md"
    note.write_text("start\n")
    app = CandatApp([note])
    async with app.run_test() as pilot:
        await pilot.pause()
        pane = app.tabs.active_pane
        editor = app.active_editor
        editor.text = "# Fresh heading\n"
        # Debounce is 0.3s; wait it out, then let the update land.
        await asyncio.sleep(0.5)
        await pilot.pause()
        assert pane.query_one(Markdown).source == "# Fresh heading\n"
