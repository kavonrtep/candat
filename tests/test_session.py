"""Tests for session persistence (reopen last files per project root)."""

import pytest

from candat import config, session
from helpers import open_app

pytestmark = pytest.mark.asyncio


def project(tmp_path):
    f1 = tmp_path / "a.py"
    f1.write_text("".join(f"line{i} = {i}\n" for i in range(50)))
    f2 = tmp_path / "notes.txt"
    f2.write_text("hello\nworld\n")
    return f1.resolve(), f2.resolve()


async def test_session_roundtrip(tmp_path):
    f1, f2 = project(tmp_path)
    async with open_app([tmp_path, f1, f2]) as (app, pilot):
        editor = app._plain_editor_for(f1)
        editor.restore_position(30, 4, 12.0)
        app.exit()  # quitting saves the session
        await pilot.pause()

    saved = session.load(tmp_path)
    assert saved is not None and len(saved["files"]) == 2
    assert saved["active"] == str(f2)  # f2 was opened last -> active

    # a fresh start on the same root, no file args -> everything comes back
    async with open_app([tmp_path]) as (app, pilot):
        paths = [e.path for e in app.all_editors()]
        assert paths == [f1, f2]
        editor = app._plain_editor_for(f1)
        assert editor.cursor_location == (30, 4)
        assert app.active_editor.path == f2


async def test_explicit_files_skip_restore(tmp_path):
    f1, f2 = project(tmp_path)
    session.save(tmp_path, [{"path": str(f1), "row": 0, "col": 0}], str(f1))
    async with open_app([tmp_path, f2]) as (app, pilot):
        paths = [e.path for e in app.all_editors()]
        assert paths == [f2]  # only the explicit file, no restore


async def test_missing_files_are_dropped(tmp_path):
    f1, _ = project(tmp_path)
    gone = tmp_path / "deleted.py"
    session.save(
        tmp_path,
        [
            {"path": str(gone), "row": 0, "col": 0},
            {"path": str(f1), "row": 1, "col": 2},
        ],
        str(gone),
    )
    async with open_app([tmp_path]) as (app, pilot):
        paths = [e.path for e in app.all_editors()]
        assert paths == [f1]


async def test_restore_can_be_disabled(tmp_path):
    f1, _ = project(tmp_path)
    session.save(tmp_path, [{"path": str(f1), "row": 0, "col": 0}], str(f1))
    cfg = config.config_path()
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("restore_session = false\n")
    async with open_app([tmp_path]) as (app, pilot):
        assert [e.path for e in app.all_editors()] == [None]  # fresh untitled


async def test_old_roots_are_pruned(tmp_path):
    for i in range(session.MAX_ROOTS + 5):
        root = tmp_path / f"root{i}"
        root.mkdir()
        session.save(root, [{"path": "/x", "row": 0, "col": 0}], None)
    all_sessions = session._load_all()
    assert len(all_sessions) == session.MAX_ROOTS
    assert str((tmp_path / "root0").resolve()) not in all_sessions  # oldest gone
    assert str((tmp_path / f"root{session.MAX_ROOTS + 4}").resolve()) in all_sessions
