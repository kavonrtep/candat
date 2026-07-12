"""Tests for safe saving: encoding + line-ending round-trip and atomicity."""

import os

import pytest

from candat.editor import (
    EditorBuffer,
    atomic_write_bytes,
    decode_text,
    detect_newline,
)
from helpers import open_app

pytestmark = pytest.mark.asyncio


async def test_decode_text_variants():
    assert decode_text(b"hello") == ("hello", "utf-8")
    assert decode_text("café".encode("utf-8")) == ("café", "utf-8")
    assert decode_text(b"\xef\xbb\xbfhi") == ("hi", "utf-8-sig")
    assert decode_text("hi".encode("utf-16")) == ("hi", "utf-16")
    # latin-1 bytes that aren't valid utf-8 fall back losslessly
    text, enc = decode_text(b"caf\xe9")  # 0xe9 = é in latin-1
    assert enc == "latin-1" and text == "café"


async def test_detect_newline():
    assert detect_newline("a\nb\n") == "\n"
    assert detect_newline("a\r\nb\r\n") == "\r\n"
    assert detect_newline("a\rb\r") == "\r"


def _roundtrip(tmp_path, name, raw: bytes):
    """Load a file into a buffer and save it straight back; return the bytes
    on disk, which must equal the input for a lossless editor."""
    path = tmp_path / name
    path.write_bytes(raw)
    editor = EditorBuffer(path=None)
    editor.load(path)
    editor.save()
    return path.read_bytes()


async def test_roundtrip_utf8_lf(tmp_path):
    raw = "line one\nlíne two\n".encode("utf-8")
    assert _roundtrip(tmp_path, "u.txt", raw) == raw


async def test_roundtrip_crlf_preserved(tmp_path):
    raw = b"one\r\ntwo\r\nthree\r\n"
    out = _roundtrip(tmp_path, "dos.txt", raw)
    assert out == raw  # CRLF not silently converted to LF


async def test_roundtrip_latin1_not_corrupted(tmp_path):
    # 0xe9 is 'é' in latin-1 and NOT valid UTF-8: the old write_text path
    # would have replaced it with U+FFFD and written mojibake back.
    raw = b"caf\xe9 menu\nsecond line\n"
    out = _roundtrip(tmp_path, "iso.txt", raw)
    assert out == raw


async def test_roundtrip_utf16(tmp_path):
    raw = "héllo\nwörld\n".encode("utf-16")  # includes a BOM
    out = _roundtrip(tmp_path, "wide.txt", raw)
    assert out.decode("utf-16") == "héllo\nwörld\n"


async def test_edit_then_save_keeps_encoding(tmp_path):
    path = tmp_path / "iso.txt"
    path.write_bytes(b"caf\xe9\n")
    editor = EditorBuffer(path=None)
    editor.load(path)
    assert editor.encoding == "latin-1"
    editor.text = "café bar\n"  # still latin-1-encodable
    editor.save()
    assert path.read_bytes() == b"caf\xe9 bar\n"


async def test_save_refuses_unencodable_character(tmp_path):
    path = tmp_path / "iso.txt"
    path.write_bytes(b"caf\xe9\n")
    editor = EditorBuffer(path=None)
    editor.load(path)
    editor.text = "emoji 🐟\n"  # not representable in latin-1
    with pytest.raises(ValueError, match="can't save as latin-1"):
        editor.save()
    # the file on disk is untouched by the failed save
    assert path.read_bytes() == b"caf\xe9\n"


async def test_atomic_write_preserves_mode_and_replaces(tmp_path):
    path = tmp_path / "x.sh"
    path.write_text("#!/bin/sh\n")
    os.chmod(path, 0o755)
    atomic_write_bytes(path, b"#!/bin/sh\necho hi\n")
    assert path.read_bytes() == b"#!/bin/sh\necho hi\n"
    assert oct(os.stat(path).st_mode)[-3:] == "755"  # executable bit kept
    # no stray temp files left behind in the directory
    assert [p.name for p in tmp_path.iterdir()] == ["x.sh"]


async def test_atomic_write_leaves_original_on_failure(tmp_path, monkeypatch):
    path = tmp_path / "keep.txt"
    original = b"original contents\n"
    path.write_text(original.decode())

    def boom(_fd):  # simulate a disk failure mid-write, after the temp exists
        raise RuntimeError("disk full")

    monkeypatch.setattr(os, "fsync", boom)
    with pytest.raises(RuntimeError):
        atomic_write_bytes(path, b"new contents that never land\n")
    assert path.read_bytes() == original  # original never touched
    assert [p.name for p in tmp_path.iterdir()] == ["keep.txt"]  # temp cleaned up


async def test_status_bar_flags_non_utf8(tmp_path):
    path = tmp_path / "iso.txt"
    path.write_bytes(b"caf\xe9\r\n")
    async with open_app([path]) as (app, pilot):
        from candat.app import StatusBar

        sb = app.query_one(StatusBar)
        sb.show(app.active_editor)
        rendered = str(sb.render())
        assert "latin-1" in rendered and "CRLF" in rendered
