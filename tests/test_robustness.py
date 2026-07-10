"""Tests for large/binary file guarding and the crash-log handler."""

from pathlib import Path

import pytest

from candat.editor import HEAD_LINES
from helpers import chord, open_app

pytestmark = pytest.mark.asyncio


async def test_large_file_opens_truncated_read_only(tmp_path):
    big = tmp_path / "huge.log"
    line = "x" * 80 + "\n"  # 81 bytes
    big.write_text(line * 150_000)  # ~12 MB
    assert big.stat().st_size > 10 * 1024 * 1024
    async with open_app([big]) as (app, pilot):
        editor = app.active_editor
        assert editor.large and editor.truncated
        assert editor.read_only
        # Only the head is loaded, not all 150k lines.
        assert editor.document.line_count <= HEAD_LINES + 2

        # Saving is refused, so it can't overwrite the file with the head.
        before = big.stat().st_size
        await chord(pilot, "ctrl+x", "ctrl+s")
        await pilot.pause()
        assert big.stat().st_size == before


async def test_binary_file_not_shown(tmp_path):
    blob = tmp_path / "data.bin"
    blob.write_bytes(bytes(range(256)) * 200)  # contains NUL bytes
    async with open_app([blob]) as (app, pilot):
        editor = app.active_editor
        assert editor.binary
        assert editor.read_only
        assert "binary" in editor.text.lower()
        assert editor.language is None


async def test_small_file_is_normal(tmp_path):
    f = tmp_path / "small.py"
    f.write_text("x = 1\n")
    async with open_app([f]) as (app, pilot):
        editor = app.active_editor
        assert not editor.large and not editor.binary and not editor.read_only
        assert editor.text == "x = 1\n"
        assert editor.language == "python"


async def test_save_guard_raises_for_truncated(tmp_path):
    big = tmp_path / "huge.txt"
    big.write_text(("y" * 100 + "\n") * 120_000)  # ~12 MB
    async with open_app([big]) as (app, pilot):
        editor = app.active_editor
        assert editor.truncated
        with pytest.raises(ValueError):
            editor.save()


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
