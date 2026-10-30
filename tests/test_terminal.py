"""Tests for the PTY terminal panel (spawns a real shell)."""

import asyncio
import os

import pytest

from candat.app import CandatApp
from candat.terminal import TerminalPane
from helpers import chord, terminal_text, wait_for

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _use_bash(bash_shell):
    """All terminal tests run against bash (conftest bash_shell fixture)."""


async def test_toggle_spawns_shell_and_runs_command():
    app = CandatApp()
    async with app.run_test() as pilot:
        terminal = app.query_one(TerminalPane)
        assert not terminal.has_class("-open")
        await chord(pilot, "ctrl+x", "t")
        assert terminal.has_class("-open")
        assert terminal.running
        assert app.focused is terminal

        for ch in "echo cand''at-works":  # quotes so the echoed command != output
            await pilot.press(ch)
        await pilot.press("enter")
        assert await wait_for(pilot, lambda: "candat-works" in terminal_text(terminal))

        # Toggle closed: hidden, focus returns to the editor.
        await chord(pilot, "ctrl+x", "t")
        assert not terminal.has_class("-open")
        assert app.focused is app.active_editor
        assert terminal.running  # shell stays alive in the background


async def test_ctrl_c_reaches_the_shell():
    app = CandatApp()
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
    app = CandatApp()
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


async def test_mouse_wheel_scrolls_history():
    from types import SimpleNamespace

    app = CandatApp()
    async with app.run_test() as pilot:
        await chord(pilot, "ctrl+x", "t")
        terminal = app.query_one(TerminalPane)
        # Produce more output than the visible screen, so history exists.
        # (Wait on the history itself: waiting on "200" in the text would
        # match the echoed command line before any output has scrolled.)
        for ch in "seq 1 200":
            await pilot.press(ch)
        await pilot.press("enter")
        assert await wait_for(
            pilot, lambda: len(terminal._screen.history.top) > 20
        )
        assert not terminal.scrolled_back
        event = SimpleNamespace(stop=lambda: None)
        terminal.on_mouse_scroll_up(event)  # wheel up -> into the history
        assert terminal.scrolled_back
        # Typing snaps back to the live view, like a real terminal.
        for ch in "echo sna''pped":
            await pilot.press(ch)
        await pilot.press("enter")
        assert await wait_for(pilot, lambda: "snapped" in terminal_text(terminal))
        assert not terminal.scrolled_back
        # Wheel down when already live is a no-op (the event isn't consumed).
        terminal.on_mouse_scroll_down(event)
        assert not terminal.scrolled_back


async def test_scrollback_history_size_from_config():
    from candat import config

    cfg = config.config_path()
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("terminal_history = 1000\n")
    app = CandatApp()
    async with app.run_test() as pilot:
        await chord(pilot, "ctrl+x", "t")
        terminal = app.query_one(TerminalPane)
        assert await wait_for(pilot, lambda: terminal.running)
        assert terminal._screen.history.size == 1000


async def test_quit_kills_shell():
    app = CandatApp()
    async with app.run_test() as pilot:
        await chord(pilot, "ctrl+x", "t")
        terminal = app.query_one(TerminalPane)
        pid = terminal._pid
        assert pid is not None
        await chord(pilot, "ctrl+x", "ctrl+c")
    # After app exit the shell must be gone (unmount kills it).
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


async def test_big_send_to_a_busy_program_never_blocks_the_ui():
    """A REPL that stops reading stdin must not freeze candat: input queues
    on a writer thread, shows in the title, and C-c discards it."""
    import time

    app = CandatApp()
    async with app.run_test() as pilot:
        await chord(pilot, "ctrl+x", "t")
        terminal = app.query_one(TerminalPane)
        assert await wait_for(pilot, lambda: terminal._seen_output)
        terminal.send_text("sleep 100\n")
        # Wait until sleep (not bash) owns the terminal: it never reads stdin.
        assert await wait_for(
            pilot, lambda: os.tcgetpgrp(terminal._fd) != terminal._pid
        )
        blob = "# filler line that bash will ignore as a comment\n" * 40_000
        started = time.monotonic()
        terminal.send_text(blob)  # ~2 MB: far beyond the pty buffer
        await pilot.pause()
        assert time.monotonic() - started < 1.0
        assert await wait_for(pilot, lambda: "queued" in (terminal.border_title or ""))
        assert terminal.queued_bytes > 1_000_000

        await pilot.press("ctrl+c")  # drops the queue and interrupts sleep
        await pilot.pause()
        assert terminal.queued_bytes <= 4096
        assert len(app.screen_stack) == 1
        assert await wait_for(pilot, lambda: terminal.queued_bytes == 0)
        # The shell recovers and takes new input. (Input arriving while bash
        # is still handling the interrupt is dropped, as in any terminal —
        # so retry rather than race it.)
        recovered = False
        for _ in range(5):
            terminal.send_text("echo don''e\n")
            if await wait_for(pilot, lambda: "done" in terminal_text(terminal), timeout=3):
                recovered = True
                break
        assert recovered
        assert await wait_for(pilot, lambda: not terminal.border_title)
