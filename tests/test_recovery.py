"""Tests for crash recovery / autosave of unsaved buffers."""

from pathlib import Path

import pytest

from candat import recovery
from helpers import chord, open_app

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
        app._autosave_recovery()
        assert recovery.pending()  # dirty buffer snapshotted
        # once saved (clean), the next autosave tick clears the snapshots
        editor.modified = False
        app._autosave_recovery()
        assert recovery.pending() == []


async def test_clean_quit_clears_recovery(tmp_path):
    f = tmp_path / "edit.py"
    f.write_text("a\n")
    async with open_app([f]) as (app, pilot):
        app.active_editor.text = "a changed\n"
        app.active_editor.modified = True
        app._autosave_recovery()
        assert recovery.pending()
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
