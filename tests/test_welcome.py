"""The startup welcome screen: shown on a bare start, gone at first use."""

from pathlib import Path

from candat.welcome import Welcome

from helpers import chord, open_app


async def test_bare_start_shows_welcome():
    async with open_app() as (app, pilot):
        pane = app.active_pane
        assert pane.is_welcome
        assert isinstance(pane.visible_widget, Welcome)
        assert app.focused is pane.welcome
        assert app.active_editor.text == ""  # the buffer itself stays empty


async def test_typing_dismisses_and_inserts():
    async with open_app() as (app, pilot):
        pane = app.active_pane
        await pilot.press("h", "i")
        assert not pane.is_welcome
        assert app.active_editor.text == "hi"
        assert app.focused is app.active_editor


async def test_escape_dismisses_without_inserting():
    async with open_app() as (app, pilot):
        pane = app.active_pane
        await pilot.press("escape")
        await pilot.pause()
        assert not pane.is_welcome
        assert app.active_editor.text == ""


async def test_opening_a_file_replaces_welcome(tmp_path: Path):
    target = tmp_path / "hello.txt"
    target.write_text("hello\n")
    async with open_app() as (app, pilot):
        pane = app.active_pane
        assert pane.is_welcome
        await app._open_path(target)
        await pilot.pause()
        assert not pane.is_welcome
        assert app.active_editor.path == target


async def test_no_welcome_when_files_are_opened(tmp_path: Path):
    target = tmp_path / "x.txt"
    target.write_text("x\n")
    async with open_app([target]) as (app, pilot):
        assert not app.active_pane.is_welcome


async def test_chords_work_from_welcome():
    async with open_app() as (app, pilot):
        # C-x t opens the terminal panel even while the splash has focus.
        await chord(pilot, "ctrl+x", "t")
        from candat.terminal import TerminalPane

        assert app.query_one(TerminalPane).has_class("-open")
