"""Tests for tier-1 features: send-to-REPL, project search, query-replace,
comment toggle."""

from pathlib import Path

import pytest
from textual.widgets import Input
from textual.widgets.text_area import Selection

from candat.app import CandatApp
from candat.projectsearch import SearchResultsScreen, search_project
from candat.replace import QueryReplaceScreen
from candat.terminal import TerminalPane
from helpers import chord, editor_with_text, make_project, wait_for

pytestmark = pytest.mark.asyncio


# -- send to REPL ---------------------------------------------------------


async def test_send_line_to_repl_opens_terminal_and_advances(monkeypatch):
    monkeypatch.setenv("SHELL", "/bin/bash")
    async with editor_with_text("echo fi''rst\necho second\n") as (app, pilot, editor):
        await chord(pilot, "ctrl+c", "ctrl+c")
        terminal = app.query_one(TerminalPane)
        assert terminal.has_class("-open")
        assert terminal.running
        assert app.focused is editor  # focus stays in the editor
        assert editor.point == (1, 0)  # cursor advanced to the next line
        # Regression: the pty must be forked at the laid-out size, never the
        # hidden-widget 20x4 fallback where output scrolls away instantly.
        assert terminal._screen.columns == terminal.content_size.width
        assert terminal._screen.lines == terminal.content_size.height
        text_of = lambda: "\n".join(terminal._screen.display)
        assert await wait_for(pilot, lambda: "first" in text_of())


async def test_send_region_to_repl(monkeypatch):
    monkeypatch.setenv("SHELL", "/bin/bash")
    async with editor_with_text("echo o''ne\necho tw''o\necho three\n") as (app, pilot, editor):
        await pilot.press("ctrl+@")
        await pilot.press("ctrl+n", "ctrl+n")  # mark first two lines
        await chord(pilot, "ctrl+c", "ctrl+c")
        terminal = app.query_one(TerminalPane)
        text_of = lambda: "\n".join(terminal._screen.display)
        assert await wait_for(pilot, lambda: "one" in text_of() and "two" in text_of())
        assert not editor.mark_active


async def test_send_before_shell_ready_is_queued(tmp_path: Path, monkeypatch):
    """Regression for the CI race: input sent before a starting shell's
    terminal setup used to be discarded by its tcsetattr flush. A slow-to-
    start shell makes the race deterministic; the text must be queued until
    the shell's first output, then flushed."""
    slow_shell = tmp_path / "slowsh"
    slow_shell.write_text("#!/bin/sh\nsleep 0.7\nexec /bin/bash\n")
    slow_shell.chmod(0o755)
    monkeypatch.setenv("SHELL", str(slow_shell))
    async with editor_with_text("echo del''ayed\n") as (app, pilot, editor):
        await chord(pilot, "ctrl+c", "ctrl+c")
        terminal = app.query_one(TerminalPane)
        assert terminal.running
        assert terminal._pending_input  # queued: shell has produced no output
        text_of = lambda: "\n".join(terminal._screen.display)
        assert await wait_for(pilot, lambda: "delayed" in text_of(), timeout=12)
        assert not terminal._pending_input


# -- comment toggle ---------------------------------------------------------


async def test_toggle_comment_line_and_region(tmp_path: Path):
    script = tmp_path / "s.py"
    script.write_text("a = 1\n\nb = 2\n")
    async with editor_with_text("", script) as (app, pilot, editor):
        await pilot.press("alt+semicolon")
        assert editor.text == "# a = 1\n\nb = 2\n"
        await pilot.press("alt+semicolon")
        assert editor.text == "a = 1\n\nb = 2\n"

        # Region: comment all three lines (blank line untouched).
        editor.selection = Selection((0, 0), (2, 5))
        editor.mark_active = True
        await pilot.press("alt+semicolon")
        assert editor.text == "# a = 1\n\n# b = 2\n"
        assert editor.mark_active  # region survives
        await pilot.press("alt+semicolon")
        assert editor.text == "a = 1\n\nb = 2\n"


async def test_toggle_comment_unsupported_language():
    async with editor_with_text("plain text\n") as (app, pilot, editor):
        await pilot.press("alt+semicolon")
        assert editor.text == "plain text\n"  # unchanged, just a warning


# -- query replace ---------------------------------------------------------


async def prompt_submit(app, pilot, value):
    prompt = app.screen.query_one(Input)
    prompt.value = value
    await pilot.press("enter")
    await pilot.pause()


async def test_query_replace_y_n_q(tmp_path: Path):
    async with editor_with_text("cat dog cat bird cat\n") as (app, pilot, editor):
        await pilot.press("alt+percent_sign")
        await pilot.pause()
        await prompt_submit(app, pilot, "cat")
        await prompt_submit(app, pilot, "fox")
        assert isinstance(app.screen, QueryReplaceScreen)
        await pilot.press("y")  # replace first
        await pilot.press("n")  # skip second
        await pilot.press("q")  # stop
        await pilot.pause()
        assert editor.text == "fox dog cat bird cat\n"


async def test_query_replace_bang_replaces_rest():
    async with editor_with_text("x1 x2 x3 x4\n") as (app, pilot, editor):
        await pilot.press("escape", "percent_sign")  # ESC % == M-%
        await pilot.pause()
        await prompt_submit(app, pilot, "x")
        await prompt_submit(app, pilot, "y")
        await pilot.press("y")  # first interactively
        await pilot.press("exclamation_mark")  # rest at once
        await pilot.pause()
        assert editor.text == "y1 y2 y3 y4\n"
        assert len(app.screen_stack) == 1


# -- project search ---------------------------------------------------------


async def test_search_project_function(tmp_path: Path):
    root = make_project(tmp_path)
    hits = search_project(root, "needle_")
    files = {p.name for p, _, _ in hits}
    assert files == {"alpha.py", "beta.py"}  # .git excluded
    beta = next(h for h in hits if h[0].name == "beta.py")
    assert beta[1] == 2 and "needle_two" in beta[2]


async def test_project_search_flow_jumps_to_match(tmp_path: Path, monkeypatch):
    root = make_project(tmp_path)
    monkeypatch.chdir(root)
    app = CandatApp([root])
    async with app.run_test() as pilot:
        await pilot.pause()
        await chord(pilot, "ctrl+x", "g")
        await prompt_submit(app, pilot, "needle_two")
        await pilot.pause()
        assert isinstance(app.screen, SearchResultsScreen)
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        editor = app.active_editor
        assert editor.path is not None and editor.path.name == "beta.py"
        assert editor.point[0] == 1  # line 2, zero-indexed
