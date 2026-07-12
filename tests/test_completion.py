"""Tests for find-file path completion with a choices list."""

import pytest
from textual.widgets import Input

from candat.dialogs import CompletionList, PromptScreen
from helpers import chord, open_app, wait_for


def hint_of(prompt) -> str:
    return str(prompt.query_one("#hint").content)

pytestmark = pytest.mark.asyncio


def make_files(tmp_path):
    (tmp_path / "alpha.py").write_text("x")
    (tmp_path / "alpha_two.py").write_text("x")
    (tmp_path / "beta.py").write_text("x")
    (tmp_path / "unique_name.md").write_text("x")
    (tmp_path / "sub").mkdir()
    return tmp_path


async def open_find_file(app, pilot, initial):
    await chord(pilot, "ctrl+x", "ctrl+f")
    prompt = app.screen
    inp = prompt.query_one(Input)
    inp.value = initial
    inp.cursor_position = len(initial)
    return prompt, inp


async def test_unique_prefix_completes_fully(tmp_path):
    root = make_files(tmp_path)
    async with open_app() as (app, pilot):
        prompt, inp = await open_find_file(app, pilot, f"{root}/uni")
        await pilot.press("tab")
        assert await wait_for(pilot, lambda: inp.value == f"{root}/unique_name.md")
        assert not prompt.completions_visible


async def test_ambiguous_shows_choices(tmp_path):
    root = make_files(tmp_path)
    async with open_app() as (app, pilot):
        prompt, inp = await open_find_file(app, pilot, f"{root}/al")
        await pilot.press("tab")
        # Common prefix filled, and the choices are listed.
        assert await wait_for(pilot, lambda: prompt.completions_visible)
        assert inp.value == f"{root}/alpha"
        options = [o.prompt for o in prompt.query_one(CompletionList)._options]
        assert options == ["alpha.py", "alpha_two.py"]


async def test_pick_from_list_fills_input(tmp_path):
    root = make_files(tmp_path)
    async with open_app() as (app, pilot):
        prompt, inp = await open_find_file(app, pilot, f"{root}/al")
        await pilot.press("tab")  # show list
        assert await wait_for(pilot, lambda: prompt.completions_visible)
        await pilot.press("tab")  # step into list
        assert await wait_for(pilot, lambda: isinstance(app.focused, CompletionList))
        await pilot.press("down")  # highlight second option
        await pilot.press("enter")  # pick it
        assert await wait_for(pilot, lambda: inp.value == f"{root}/alpha_two.py")
        assert not prompt.completions_visible
        assert isinstance(app.focused, Input)


async def test_typing_dismisses_the_list(tmp_path):
    root = make_files(tmp_path)
    async with open_app() as (app, pilot):
        prompt, inp = await open_find_file(app, pilot, f"{root}/al")
        await pilot.press("tab")
        assert await wait_for(pilot, lambda: prompt.completions_visible)
        await pilot.press("p")  # keep typing
        assert await wait_for(pilot, lambda: not prompt.completions_visible)


async def test_escape_in_list_returns_to_input_without_cancelling(tmp_path):
    root = make_files(tmp_path)
    async with open_app() as (app, pilot):
        prompt, inp = await open_find_file(app, pilot, f"{root}/al")
        await pilot.press("tab")
        assert await wait_for(pilot, lambda: prompt.completions_visible)
        await pilot.press("tab")  # into list
        assert await wait_for(pilot, lambda: isinstance(app.focused, CompletionList))
        await pilot.press("escape")  # closes list only
        assert await wait_for(pilot, lambda: not prompt.completions_visible)
        assert isinstance(app.screen, PromptScreen)  # prompt still open
        # A second Escape cancels the prompt.
        await pilot.press("escape")
        assert await wait_for(pilot, lambda: not isinstance(app.screen, PromptScreen))


async def test_prefilled_path_not_selected_backspace_deletes_one(tmp_path):
    async with open_app() as (app, pilot):
        await chord(pilot, "ctrl+x", "ctrl+f")
        inp = app.screen.query_one(Input)
        initial = inp.value
        assert initial.endswith("/")
        # Caret at end, nothing selected — so Backspace deletes a single char
        # (walking up a level) instead of wiping the whole selected path.
        assert inp.cursor_position == len(initial)
        assert inp.selection.is_empty
        await pilot.press("backspace")
        assert inp.value == initial[:-1]


async def test_no_match_shows_hint(tmp_path):
    root = make_files(tmp_path)
    async with open_app() as (app, pilot):
        prompt, inp = await open_find_file(app, pilot, f"{root}/zzz")
        # Drain the value-set's Input.Changed (which clears the hint) before
        # Tab, so it can't land after Tab's show_hint and wipe "[no match]"
        # (it did exactly that on the slower macOS runner).
        await pilot.pause()
        await pilot.press("tab")
        assert await wait_for(pilot, lambda: "no match" in hint_of(prompt))
        assert not prompt.completions_visible
