"""Tests for the live markdown preview.

The preview renders in a background thread and blits pre-rendered line
strips, so tests wait for the rendered text to appear rather than querying
a widget tree.
"""

import asyncio
from pathlib import Path

import pytest

from candat.app import CandatApp
from candat.preview import PREVIEW_MAX_BYTES
from helpers import chord, wait_for

pytestmark = pytest.mark.asyncio


async def test_markdown_opens_in_split_preview(tmp_path: Path):
    note = tmp_path / "note.md"
    note.write_text("# Title\n\nSome text.\n")
    app = CandatApp([note])
    async with app.run_test() as pilot:
        await pilot.pause()
        pane = app.tabs.active_pane
        assert pane.preview_mode == "split"
        preview = pane.preview
        assert preview.styles.display != "none"
        # The background render lands shortly after mount.
        assert await wait_for(pilot, lambda: "Title" in preview.plain_text())


async def test_non_markdown_has_no_preview(tmp_path: Path):
    code = tmp_path / "x.py"
    code.write_text("print(1)\n")
    app = CandatApp([code])
    async with app.run_test() as pilot:
        await pilot.pause()
        pane = app.tabs.active_pane
        assert pane.preview_mode == "off"
        # C-c C-v refuses politely.
        await chord(pilot, "ctrl+c", "ctrl+v")
        assert pane.preview_mode == "off"


async def test_toggle_cycles_modes(tmp_path: Path):
    note = tmp_path / "note.md"
    note.write_text("hello\n")
    app = CandatApp([note])
    async with app.run_test() as pilot:
        await pilot.pause()
        pane = app.tabs.active_pane
        assert pane.preview_mode == "split"
        await chord(pilot, "ctrl+c", "ctrl+v")
        assert pane.preview_mode == "only"
        assert app.focused is pane.preview
        await chord(pilot, "ctrl+c", "ctrl+v")
        assert pane.preview_mode == "off"
        assert app.focused is app.active_editor
        await chord(pilot, "ctrl+c", "ctrl+v")
        assert pane.preview_mode == "split"


async def test_preview_updates_after_debounce(tmp_path: Path):
    note = tmp_path / "note.md"
    note.write_text("start\n")
    app = CandatApp([note])
    async with app.run_test() as pilot:
        await pilot.pause()
        pane = app.tabs.active_pane
        editor = app.active_editor
        editor.text = "# Fresh heading\n"
        # Debounce is 0.3s; the background render lands after it.
        await asyncio.sleep(0.5)
        assert await wait_for(
            pilot, lambda: "Fresh heading" in pane.preview.plain_text()
        )


async def test_rapid_edits_coalesce_to_latest(tmp_path: Path):
    note = tmp_path / "note.md"
    note.write_text("start\n")
    app = CandatApp([note])
    async with app.run_test() as pilot:
        await pilot.pause()
        pane = app.tabs.active_pane
        # Several generations before any render can finish: only the last
        # text must end up in the preview.
        for i in range(5):
            await pane.preview.render_text(f"version {i}\n")
        assert await wait_for(pilot, lambda: "version 4" in pane.preview.plain_text())


async def test_huge_document_shows_placeholder(tmp_path: Path):
    note = tmp_path / "big.md"
    note.write_text("start\n")
    app = CandatApp([note])
    async with app.run_test() as pilot:
        await pilot.pause()
        pane = app.tabs.active_pane
        big = "word " * (PREVIEW_MAX_BYTES // 5 + 100)
        await pane.preview.render_text(big)
        assert await wait_for(
            pilot, lambda: "Preview disabled" in pane.preview.plain_text()
        )


async def test_preview_scrolls_with_content(tmp_path: Path):
    note = tmp_path / "long.md"
    note.write_text("\n\n".join(f"## Head {i}" for i in range(200)))
    app = CandatApp([note])
    async with app.run_test() as pilot:
        await pilot.pause()
        pane = app.tabs.active_pane
        preview = pane.preview
        assert await wait_for(pilot, lambda: "Head 199" in preview.plain_text())
        assert preview.max_scroll_y > 0  # virtual size covers the document
