"""Meta-key handling: real-terminal alt events, ESC prefix, line moving,
and the linked preview scroll."""

import asyncio
from pathlib import Path

import pytest
from textual import events
from textual.widgets.text_area import Selection

from candat.app import CandatApp
from candat.preview import MarkdownPreview

pytestmark = pytest.mark.asyncio


async def app_with_text(text: str):
    app = CandatApp()
    pilot_cm = app.run_test()
    pilot = await pilot_cm.__aenter__()
    editor = app.active_editor
    editor.text = text
    editor.selection = Selection((0, 0), (0, 0))
    await pilot.pause()
    return app, pilot, pilot_cm, editor


async def press_real_alt(pilot, editor, key: str, character: str | None):
    """Deliver an alt+key event the way a real terminal parser does: with the
    base character attached (the pilot sends character=None, which hid a bug
    where TextArea self-inserted the letter)."""
    editor.post_message(events.Key(key, character))
    await pilot.pause()


async def test_real_alt_w_copies_region_instead_of_typing_w():
    app, pilot, cm, editor = await app_with_text("hello world\n")
    try:
        await pilot.press("ctrl+@")
        for _ in range(5):
            await pilot.press("ctrl+f")
        assert editor.selected_text == "hello"
        await press_real_alt(pilot, editor, "alt+w", "w")
        assert editor.text == "hello world\n"  # no stray "w" inserted
        assert app.kill_ring.current == "hello"
        assert not editor.mark_active
    finally:
        await cm.__aexit__(None, None, None)


async def test_real_alt_d_and_f_do_not_self_insert():
    app, pilot, cm, editor = await app_with_text("one two three\n")
    try:
        await press_real_alt(pilot, editor, "alt+f", "f")
        assert editor.point == (0, 3)
        await press_real_alt(pilot, editor, "alt+d", "d")
        assert editor.text == "one three\n"
        assert app.kill_ring.current == " two"
    finally:
        await cm.__aexit__(None, None, None)


async def test_escape_acts_as_meta_prefix():
    app, pilot, cm, editor = await app_with_text("hello world\n")
    try:
        # ESC w == M-w (kill-ring-save), as in emacs.
        await pilot.press("ctrl+@")
        for _ in range(5):
            await pilot.press("ctrl+f")
        await pilot.press("escape", "w")
        assert editor.text == "hello world\n"
        assert app.kill_ring.current == "hello"

        # ESC up == M-up: alt+arrows arrive as escape+arrow in real terminals.
        editor.selection = Selection((1, 0), (1, 0))
        editor.text = "aaa\nbbb\n"
        editor.selection = Selection((1, 0), (1, 0))
        await pilot.press("escape", "up")
        assert editor.text == "bbb\naaa\n"
    finally:
        await cm.__aexit__(None, None, None)


async def test_move_line_up_down():
    app, pilot, cm, editor = await app_with_text("one\ntwo\nthree\n")
    try:
        editor.selection = Selection((1, 1), (1, 1))  # cursor on "two"
        await pilot.press("alt+up")
        assert editor.text == "two\none\nthree\n"
        assert editor.point == (0, 1)
        await pilot.press("alt+up")  # already at top: no-op
        assert editor.text == "two\none\nthree\n"
        await pilot.press("alt+down", "alt+down")
        assert editor.text == "one\nthree\ntwo\n"
        assert editor.point == (2, 1)
    finally:
        await cm.__aexit__(None, None, None)


async def test_move_region_block_down_keeps_region():
    app, pilot, cm, editor = await app_with_text("a\nb\nc\nd\n")
    try:
        # Mark lines a+b (region ends at column 0 of line c -> c not included).
        await pilot.press("ctrl+@")
        await pilot.press("ctrl+n", "ctrl+n")
        assert editor.selected_text == "a\nb\n"
        await pilot.press("alt+down")
        assert editor.text == "c\na\nb\nd\n"
        assert editor.mark_active
        assert editor.selected_text == "a\nb\n"
        await pilot.press("alt+down")
        assert editor.text == "c\nd\na\nb\n"
    finally:
        await cm.__aexit__(None, None, None)


async def test_preview_scroll_follows_editor(tmp_path: Path):
    note = tmp_path / "long.md"
    note.write_text("\n\n".join(f"## Section {i}\n\ntext {i}" for i in range(60)))
    app = CandatApp([note])
    async with app.run_test(size=(100, 24)) as pilot:
        await pilot.pause()
        editor = app.active_editor
        pane = app.tabs.active_pane
        preview = pane.query_one(MarkdownPreview)
        assert preview.scroll_y == 0
        editor.scroll_to(y=editor.max_scroll_y, animate=False)
        await pilot.pause()
        await pilot.pause()
        assert preview.scroll_y > 0
        assert abs(preview.scroll_y - preview.max_scroll_y) < 2
