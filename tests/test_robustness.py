"""Tests for large/binary file guarding and the crash-log handler."""

from pathlib import Path

import pytest

from candat.editor import classify_file, read_file_head
from helpers import chord, open_app

pytestmark = pytest.mark.asyncio


async def test_classify_file(tmp_path):
    small = tmp_path / "s.txt"
    small.write_text("hi\n")
    assert classify_file(small)[0] == "normal"

    big = tmp_path / "big.txt"
    big.write_text(("x" * 80 + "\n") * 150_000)  # ~12 MB
    assert classify_file(big) == ("large", big.stat().st_size)

    blob = tmp_path / "b.bin"
    blob.write_bytes(bytes(range(256)) * 200)
    assert classify_file(blob)[0] == "binary"

    # read_file_head guards huge/binary too
    assert read_file_head(big)[1] == "large"
    assert read_file_head(blob)[1] == "binary"
    # force_full reads a large text file whole (but still guards binary)
    text, kind = read_file_head(big, force_full=True)[:2]
    assert kind == "normal" and text.count("\n") == 150_000
    assert read_file_head(blob, force_full=True)[1] == "binary"


async def test_read_only_guard_lifts_when_file_shrinks(tmp_path):
    """The large-file guard must not leave a buffer stuck read-only after a
    reload brings it back to a normal, fully loaded state."""
    blob = tmp_path / "b.bin"
    blob.write_bytes(bytes(range(256)) * 200)  # binary → guarded placeholder
    async with open_app([blob]) as (app, pilot):
        editor = app.active_editor
        assert editor.binary and editor.read_only
        blob.write_text("plain text now\n")
        editor.reload_from_disk()
        assert not editor.binary and not editor.large
        assert not editor.read_only  # the guard lifted
        assert editor.text == "plain text now\n"
        # ...but a user-toggled read-only on a normal file is left alone
        editor.read_only = True
        blob.write_text("still plain\n")
        editor.reload_from_disk()
        assert editor.read_only


async def test_binary_file_not_shown_and_unsaveable(tmp_path):
    blob = tmp_path / "data.bin"
    blob.write_bytes(bytes(range(256)) * 200)  # contains NUL bytes
    original = blob.read_bytes()
    async with open_app([blob]) as (app, pilot):
        editor = app.active_editor
        assert editor.binary and editor.read_only
        assert "binary" in editor.text.lower()
        assert editor.language is None
        # save is refused → the file is untouched
        await chord(pilot, "ctrl+x", "ctrl+s")
        await pilot.pause()
        assert blob.read_bytes() == original


async def test_small_file_is_normal(tmp_path):
    f = tmp_path / "small.py"
    f.write_text("x = 1\n")
    async with open_app([f]) as (app, pilot):
        editor = app.active_editor
        assert not editor.large and not editor.binary and not editor.read_only
        assert editor.text == "x = 1\n"
        assert editor.language == "python"


async def test_write_crash_log(tmp_path, monkeypatch):
    from candat.app import _write_crash_log

    monkeypatch.setenv("HOME", str(tmp_path))
    try:
        raise ValueError("kaboom-xyz")
    except ValueError as error:
        log = _write_crash_log(error)
    assert log.exists()
    assert log.parent == tmp_path / ".cache" / "candat"
    text = log.read_text()
    assert "kaboom-xyz" in text and "Traceback" in text
