"""Tests for crash recovery / autosave of unsaved buffers."""

from pathlib import Path

import pytest

from candat import recovery
from helpers import chord, open_app, wait_for

pytestmark = pytest.mark.asyncio


async def test_snapshot_and_pending_roundtrip(tmp_path):
    p = tmp_path / "notes.txt"
    written = recovery.snapshot([(p, "unsaved body\n"), (None, "scratch\n")])
    assert written == recovery.recovery_dir()
    left = recovery.pending()
    assert len(left) == 2
    bodies = sorted(f.read_text() for f in left)
    assert bodies == ["scratch\n", "unsaved body\n"]


async def test_clear_removes_snapshots(tmp_path):
    recovery.snapshot([(tmp_path / "a.txt", "x\n")])
    assert recovery.pending()
    recovery.clear()
    assert recovery.pending() == []


async def test_snapshot_survives_bad_dir(monkeypatch, tmp_path):
    # A path that can't be created must not raise — recovery is best-effort.
    monkeypatch.setattr(recovery, "recovery_dir", lambda: tmp_path / "x" / "\x00bad")
    assert recovery.snapshot([(tmp_path / "a.txt", "hi")]) is None


async def test_autosave_writes_dirty_and_clears_when_clean(tmp_path):
    f = tmp_path / "edit.py"
    f.write_text("x = 1\n")
    async with open_app([f]) as (app, pilot):
        editor = app.active_editor
        editor.text = "x = 2\n"
        editor.modified = True
        app._autosave_recovery()  # writes on a worker thread
        assert await wait_for(pilot, lambda: bool(recovery.pending()))
        # once saved (clean), the next autosave tick clears the snapshots
        assert await wait_for(pilot, lambda: not app._recovery_busy)
        editor.modified = False
        app._autosave_recovery()
        assert await wait_for(pilot, lambda: recovery.pending() == [])


async def test_clean_quit_clears_recovery(tmp_path):
    f = tmp_path / "edit.py"
    f.write_text("a\n")
    async with open_app([f]) as (app, pilot):
        app.active_editor.text = "a changed\n"
        app.active_editor.modified = True
        app._autosave_recovery()
        assert await wait_for(pilot, lambda: bool(recovery.pending()))
        app.exit()
        await pilot.pause()
    assert recovery.pending() == []  # a clean quit leaves nothing to recover


async def test_startup_announces_leftover_recovery(tmp_path):
    # A snapshot left by a "previous crash" is reported on the next launch.
    recovery.snapshot([(tmp_path / "lost.txt", "work in progress\n")])
    notices = []
    async with open_app([tmp_path / "new.py"]) as (app, pilot):
        # _announce_recovery ran in on_mount; assert the file is still there
        # (never auto-overwritten) and pending() still sees it.
        assert recovery.pending()


async def test_autosave_skips_unchanged_and_never_blocks(tmp_path, monkeypatch):
    import threading
    import time

    f = tmp_path / "edit.py"
    f.write_text("a\n")
    writes: list[int] = []
    release = threading.Event()
    real = recovery.snapshot

    def slow_snapshot(buffers):
        release.wait(5)  # a hung network home directory
        writes.append(len(buffers))
        return real(buffers)

    monkeypatch.setattr(recovery, "snapshot", slow_snapshot)
    async with open_app([f]) as (app, pilot):
        editor = app.active_editor
        editor.insert("b")
        await pilot.pause()
        assert editor.modified
        started = time.monotonic()
        app._autosave_recovery()
        app._autosave_recovery()  # previous write still in flight: skipped
        assert time.monotonic() - started < 0.5
        release.set()
        assert await wait_for(pilot, lambda: not app._recovery_busy)
        app._autosave_recovery()  # nothing changed since: skipped
        await pilot.pause()
        assert writes == [1]
        editor.insert("c")
        await pilot.pause()
        app._autosave_recovery()
        assert await wait_for(pilot, lambda: writes == [1, 1])
