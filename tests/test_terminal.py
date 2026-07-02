"""Tests for the PTY terminal panel (spawns a real shell)."""

import asyncio
import os

import pytest

from pike.app import PikeApp
from pike.terminal import TerminalPane

pytestmark = pytest.mark.asyncio


async def chord(pilot, *keys):
    for key in keys:
        await pilot.press(key)
        await pilot.pause()
    await pilot.pause()


def terminal_text(terminal: TerminalPane) -> str:
    return "\n".join(terminal._screen.display) if terminal._screen else ""


async def wait_for(pilot, predicate, timeout=8.0):
    elapsed = 0.0
    while elapsed < timeout:
        await pilot.pause()
        if predicate():
            return True
        await asyncio.sleep(0.1)
        elapsed += 0.1
    return False


@pytest.fixture(autouse=True)
def plain_shell(monkeypatch):
    monkeypatch.setenv("SHELL", "/bin/bash")
    monkeypatch.setenv("PS1", "$ ")


async def test_toggle_spawns_shell_and_runs_command():
    app = PikeApp()
    async with app.run_test() as pilot:
        terminal = app.query_one(TerminalPane)
        assert not terminal.has_class("-open")
        await chord(pilot, "ctrl+x", "t")
        assert terminal.has_class("-open")
        assert terminal.running
        assert app.focused is terminal

        for ch in "echo pik''e-works":  # quotes so the echoed command != output
            await pilot.press(ch)
        await pilot.press("enter")
        assert await wait_for(pilot, lambda: "pike-works" in terminal_text(terminal))

        # Toggle closed: hidden, focus returns to the editor.
        await chord(pilot, "ctrl+x", "t")
        assert not terminal.has_class("-open")
        assert app.focused is app.active_editor
        assert terminal.running  # shell stays alive in the background


async def test_ctrl_c_reaches_the_shell():
    app = PikeApp()
    async with app.run_test() as pilot:
        await chord(pilot, "ctrl+x", "t")
        terminal = app.query_one(TerminalPane)
        for ch in "sleep 100":
            await pilot.press(ch)
        await pilot.press("enter")
        await asyncio.sleep(0.3)
        # C-c must interrupt sleep, not open the C-c chord.
        await pilot.press("ctrl+c")
        await pilot.pause()
        assert len(app.screen_stack) == 1
        for ch in "echo don''e":
            await pilot.press(ch)
        await pilot.press("enter")
        assert await wait_for(pilot, lambda: "done" in terminal_text(terminal))


async def test_shell_exit_and_respawn():
    app = PikeApp()
    async with app.run_test() as pilot:
        await chord(pilot, "ctrl+x", "t")
        terminal = app.query_one(TerminalPane)
        for ch in "exit":
            await pilot.press(ch)
        await pilot.press("enter")
        assert await wait_for(pilot, lambda: not terminal.running)
        # Close and reopen: a fresh shell is spawned.
        await chord(pilot, "ctrl+x", "t")
        await chord(pilot, "ctrl+x", "t")
        assert terminal.running


async def test_quit_kills_shell():
    app = PikeApp()
    async with app.run_test() as pilot:
        await chord(pilot, "ctrl+x", "t")
        terminal = app.query_one(TerminalPane)
        pid = terminal._pid
        assert pid is not None
        await chord(pilot, "ctrl+x", "ctrl+c")
    # After app exit the shell must be gone (unmount kills it).
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
