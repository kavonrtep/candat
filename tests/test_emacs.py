"""Tests for the emacs editing layer: kill ring, mark/region, isearch, M-x."""


import pytest

from candat.app import CandatApp
from helpers import chord, editor_with_text

pytestmark = pytest.mark.asyncio


async def test_movement_keys():
    async with editor_with_text("alpha beta\ngamma delta\n") as (app, pilot, editor):
        await pilot.press("ctrl+f", "ctrl+f")
        assert editor.point == (0, 2)
        await pilot.press("ctrl+n")
        assert editor.point[0] == 1
        await pilot.press("ctrl+p", "ctrl+b")
        assert editor.point == (0, 1)
        await pilot.press("ctrl+e")
        assert editor.point == (0, 10)
        await pilot.press("ctrl+a")
        assert editor.point == (0, 0)
        await pilot.press("alt+f")
        assert editor.point == (0, 5)
        await pilot.press("alt+greater_than_sign")
        assert editor.point == editor.document.end
        await pilot.press("alt+less_than_sign")
        assert editor.point == (0, 0)


async def test_kill_line_and_yank():
    async with editor_with_text("first line\nsecond line\n") as (app, pilot, editor):
        # C-k kills to end of line; second C-k kills the newline (appends).
        await pilot.press("ctrl+k")
        assert editor.text == "\nsecond line\n"
        await pilot.press("ctrl+k")
        assert editor.text == "second line\n"
        assert app.kill_ring.current == "first line\n"
        # Yank it back.
        await pilot.press("ctrl+y")
        assert editor.text == "first line\nsecond line\n"
        assert editor.point == (1, 0)


async def test_mark_region_kill_and_copy():
    async with editor_with_text("hello world\n") as (app, pilot, editor):
        # Set mark, extend region with plain movement, kill it.
        await pilot.press("ctrl+@")
        assert editor.mark_active
        for _ in range(5):
            await pilot.press("ctrl+f")
        assert editor.selected_text == "hello"
        await pilot.press("ctrl+w")
        assert editor.text == " world\n"
        assert app.kill_ring.current == "hello"
        assert not editor.mark_active

        # M-w copies without deleting.
        await pilot.press("ctrl+@")
        await pilot.press("alt+f")
        await pilot.press("alt+w")
        assert editor.text == " world\n"
        assert app.kill_ring.current == " world"
        assert not editor.mark_active


async def test_typing_deactivates_mark_without_deleting():
    async with editor_with_text("abc\n") as (app, pilot, editor):
        await pilot.press("ctrl+@")
        await pilot.press("ctrl+f", "ctrl+f")
        assert editor.selected_text == "ab"
        await pilot.press("x")
        # Emacs inserts at point without deleting the region.
        assert editor.text == "abxc\n"
        assert not editor.mark_active


async def test_yank_pop_rotates_kill_ring():
    async with editor_with_text("one two\n") as (app, pilot, editor):
        await pilot.press("alt+d")  # kill "one"
        await pilot.press("ctrl+f")
        await pilot.press("alt+d")  # kill "two" (not consecutive: movement between)
        assert app.kill_ring.current == "two"
        assert editor.text == " \n"
        await pilot.press("ctrl+y")
        assert editor.text == " two\n"
        await pilot.press("alt+y")  # yank-pop -> previous kill
        assert editor.text == " one\n"


async def test_kill_word_backward_prepends():
    async with editor_with_text("foo bar\n") as (app, pilot, editor):
        await pilot.press("ctrl+e")
        await pilot.press("alt+backspace")
        assert app.kill_ring.current == "bar"
        await pilot.press("alt+backspace")
        # Consecutive backward kills prepend into one entry.
        assert app.kill_ring.current == "foo bar"
        assert editor.text == "\n"


async def test_exchange_point_and_mark_and_undo_chords():
    async with editor_with_text("hello\n") as (app, pilot, editor):
        await pilot.press("ctrl+@")
        await pilot.press("ctrl+f", "ctrl+f", "ctrl+f")
        assert editor.point == (0, 3)
        await chord(pilot, "ctrl+x", "ctrl+x")
        assert editor.point == (0, 0)
        assert editor.mark == (0, 3)

        # C-x h selects everything.
        await chord(pilot, "ctrl+x", "h")
        assert editor.selected_text == "hello\n"

        # C-g deactivates the mark.
        await pilot.press("ctrl+g")
        assert not editor.mark_active
        assert editor.selected_text == ""

        # C-x u undoes an edit.
        await pilot.press("alt+greater_than_sign", "z")
        assert "z" in editor.text
        await chord(pilot, "ctrl+x", "u")
        assert editor.text == "hello\n"


async def test_isearch_forward_and_accept():
    async with editor_with_text("alpha beta\ngamma beta end\n") as (app, pilot, editor):
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert len(app.screen_stack) == 2
        await pilot.press("b", "e", "t", "a")
        assert editor.selected_text == "beta"
        assert editor.selection.end == (0, 10)
        # Next match.
        await pilot.press("ctrl+s")
        assert editor.selection.end == (1, 10)
        # Wraps around.
        await pilot.press("ctrl+s")
        assert editor.selection.end == (0, 10)
        # Accept: point stays, mark left at origin, screen popped.
        await pilot.press("enter")
        await pilot.pause()
        assert len(app.screen_stack) == 1
        assert editor.point == (0, 10)
        assert editor.mark == (0, 0)
        assert app.last_search == "beta"


async def test_isearch_highlights_all_visible_matches():
    async with editor_with_text("alpha beta\ngamma beta end\nno match\n") as (
        app,
        pilot,
        editor,
    ):
        def match_spans() -> int:
            return sum(
                sum(1 for s in editor.get_line(i).spans if s.style == editor.SEARCH_STYLE)
                for i in range(editor.document.line_count)
            )

        assert match_spans() == 0
        await pilot.press("ctrl+s")
        await pilot.press("b", "e", "t", "a")
        await pilot.pause()
        assert match_spans() == 2  # both 'beta' occurrences highlighted, not just one
        # Lazy-highlight clears when the search ends.
        await pilot.press("enter")
        await pilot.pause()
        assert match_spans() == 0


async def test_isearch_cancel_restores_origin():
    async with editor_with_text("alpha beta\n") as (app, pilot, editor):
        await pilot.press("ctrl+s")
        await pilot.pause()
        await pilot.press("b", "e")
        assert editor.point != (0, 0)
        await pilot.press("ctrl+g")
        await pilot.pause()
        assert len(app.screen_stack) == 1
        assert editor.point == (0, 0)


async def test_isearch_backward():
    async with editor_with_text("beta one beta two\n") as (app, pilot, editor):
        await pilot.press("alt+greater_than_sign")
        await pilot.press("ctrl+r")
        await pilot.pause()
        await pilot.press("b", "e", "t", "a")
        # Point at the start of the match when searching backward.
        assert editor.selected_text == "beta"
        assert editor.point == (0, 9)
        await pilot.press("ctrl+r")
        assert editor.point == (0, 0)
        await pilot.press("enter")
        await pilot.pause()


async def test_command_palette_opens_on_alt_x():
    app = CandatApp()
    async with app.run_test() as pilot:
        await pilot.press("alt+x")
        await pilot.pause()
        assert app.screen.__class__.__name__ == "CommandPalette"
        await pilot.press("escape")
