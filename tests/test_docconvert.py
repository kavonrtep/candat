"""Word-processor documents (.docx/.odt) open as rendered markdown."""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

import pytest
from helpers import chord, open_app, wait_for

from candat import docconvert
from candat.app import StatusBar
from candat.docconvert import ConversionError, convert_to_markdown
from candat.pane import unique_labels

# -- fixtures: minimal but realistic documents ---------------------------------

DOCX_DOCUMENT = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
            xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<w:body>
<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Report title</w:t></w:r></w:p>
<w:p><w:r><w:t xml:space="preserve">Plain text with </w:t></w:r>
     <w:r><w:rPr><w:b/></w:rPr><w:t>bo</w:t></w:r>
     <w:r><w:rPr><w:b/></w:rPr><w:t>ld</w:t></w:r>
     <w:r><w:t xml:space="preserve"> and </w:t></w:r>
     <w:r><w:rPr><w:i/></w:rPr><w:t>italic</w:t></w:r>
     <w:r><w:t>.</w:t></w:r></w:p>
<w:p><w:pPr><w:pStyle w:val="Heading2"/></w:pPr><w:r><w:t>Items</w:t></w:r></w:p>
<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:t>first</w:t></w:r></w:p>
<w:p><w:pPr><w:numPr><w:ilvl w:val="1"/><w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:t>nested</w:t></w:r></w:p>
<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="2"/></w:numPr></w:pPr><w:r><w:t>step one</w:t></w:r></w:p>
<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="2"/></w:numPr></w:pPr><w:r><w:t>step two</w:t></w:r></w:p>
<w:p><w:r><w:t xml:space="preserve">See </w:t></w:r>
     <w:hyperlink r:id="rId5"><w:r><w:t>the site</w:t></w:r></w:hyperlink></w:p>
<w:tbl>
 <w:tr><w:tc><w:p><w:r><w:t>Name</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Value</w:t></w:r></w:p></w:tc></w:tr>
 <w:tr><w:tc><w:p><w:r><w:t>a</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>1</w:t></w:r></w:p></w:tc></w:tr>
</w:tbl>
<w:p><w:r><w:t>The end.</w:t></w:r></w:p>
</w:body></w:document>
"""

DOCX_NUMBERING = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0"><w:numFmt w:val="bullet"/></w:lvl><w:lvl w:ilvl="1"><w:numFmt w:val="bullet"/></w:lvl></w:abstractNum>
<w:abstractNum w:abstractNumId="1"><w:lvl w:ilvl="0"><w:numFmt w:val="decimal"/></w:lvl></w:abstractNum>
<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>
<w:num w:numId="2"><w:abstractNumId w:val="1"/></w:num>
</w:numbering>
"""

DOCX_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId5" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="https://example.org/" TargetMode="External"/>
</Relationships>
"""

DOCX_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>
</Types>
"""

DOCX_ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>
"""


def write_docx(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", DOCX_CONTENT_TYPES)
        archive.writestr("_rels/.rels", DOCX_ROOT_RELS)
        archive.writestr("word/document.xml", DOCX_DOCUMENT)
        archive.writestr("word/numbering.xml", DOCX_NUMBERING)
        archive.writestr("word/_rels/document.xml.rels", DOCX_RELS)
    return path


ODT_CONTENT = """<?xml version="1.0" encoding="UTF-8"?>
<office:document-content
  xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
  xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0"
  xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
  xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"
  xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0"
  xmlns:xlink="http://www.w3.org/1999/xlink" office:version="1.3">
<office:automatic-styles>
 <style:style style:name="T1" style:family="text"><style:text-properties fo:font-weight="bold"/></style:style>
 <style:style style:name="T2" style:family="text"><style:text-properties fo:font-style="italic"/></style:style>
 <text:list-style style:name="L1"><text:list-level-style-bullet text:level="1" text:bullet-char="•"/></text:list-style>
 <text:list-style style:name="L2"><text:list-level-style-number text:level="1" style:num-format="1"/></text:list-style>
</office:automatic-styles>
<office:body><office:text>
<text:h text:outline-level="1">Report title</text:h>
<text:p>Plain text with <text:span text:style-name="T1">bold</text:span> and <text:span text:style-name="T2">italic</text:span>.</text:p>
<text:h text:outline-level="2">Items</text:h>
<text:list text:style-name="L1">
 <text:list-item><text:p>first</text:p>
  <text:list><text:list-item><text:p>nested</text:p></text:list-item></text:list>
 </text:list-item>
</text:list>
<text:list text:style-name="L2">
 <text:list-item><text:p>step one</text:p></text:list-item>
 <text:list-item><text:p>step two</text:p></text:list-item>
</text:list>
<text:p>See <text:a xlink:href="https://example.org/">the site</text:a></text:p>
<table:table table:name="T">
 <table:table-row><table:table-cell><text:p>Name</text:p></table:table-cell><table:table-cell><text:p>Value</text:p></table:table-cell></table:table-row>
 <table:table-row><table:table-cell><text:p>a</text:p></table:table-cell><table:table-cell><text:p>1</text:p></table:table-cell></table:table-row>
</table:table>
<text:p>The end.</text:p>
</office:text></office:body></office:document-content>
"""


def write_odt(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/vnd.oasis.opendocument.text")
        archive.writestr("content.xml", ODT_CONTENT)
    return path


@pytest.fixture
def builtin_only(monkeypatch):
    """Force the stdlib converter even when pandoc is installed."""
    monkeypatch.setattr(docconvert.shutil, "which", lambda name: None)


# -- built-in converter ---------------------------------------------------------


def check_common_shape(md: str) -> None:
    assert "# Report title" in md
    assert "**bold**" in md
    assert "*italic*" in md
    assert "## Items" in md
    assert "- first" in md
    assert "  - nested" in md
    assert "1. step one" in md
    assert "2. step two" in md
    assert "[the site](https://example.org/)" in md
    assert "| Name | Value |" in md
    assert "| a | 1 |" in md
    assert md.rstrip().endswith("The end.")


def test_builtin_docx(tmp_path, builtin_only):
    md = convert_to_markdown(write_docx(tmp_path / "r.docx"))
    check_common_shape(md)
    # Split bold runs merge; the "and" between them is not bold.
    assert "**bo****ld**" not in md
    assert "Plain text with **bold** and *italic*." in md


def test_builtin_odt(tmp_path, builtin_only):
    md = convert_to_markdown(write_odt(tmp_path / "r.odt"))
    check_common_shape(md)
    assert "Plain text with **bold** and *italic*." in md


def test_bad_zip_raises(tmp_path, builtin_only):
    broken = tmp_path / "broken.docx"
    broken.write_bytes(b"PK\x03\x04 this is not really a zip")
    with pytest.raises(ConversionError):
        convert_to_markdown(broken)


def test_unsupported_suffix_raises(tmp_path):
    with pytest.raises(ConversionError):
        convert_to_markdown(tmp_path / "notes.txt")


@pytest.mark.skipif(shutil.which("pandoc") is None, reason="pandoc not installed")
def test_pandoc_docx(tmp_path):
    md = convert_to_markdown(write_docx(tmp_path / "r.docx"))
    assert "Report title" in md
    assert "bold" in md and "The end." in md


# -- in the app ------------------------------------------------------------------


async def test_docx_opens_as_rendered_markdown(tmp_path, builtin_only):
    path = write_docx(tmp_path / "report.docx")
    async with open_app([path]) as (app, pilot):
        pane = app.active_pane
        editor = pane.editor
        assert editor.document_view
        assert editor.read_only
        assert editor.path == path
        assert editor.language == "markdown"
        assert "# Report title" in editor.text
        # Shown rendered: preview-only, with the preview focused.
        assert pane.preview_mode == "only"
        assert await wait_for(pilot, lambda: "Report title" in pane.preview.plain_text())
        assert "document" in str(app.query_one(StatusBar).render())
        # C-c C-v cycles to the markdown source; the editor stays read-only.
        await chord(pilot, "ctrl+c", "ctrl+v")
        assert pane.preview_mode == "off"
        assert editor.read_only


async def test_document_is_never_written_back(tmp_path, builtin_only):
    path = write_docx(tmp_path / "report.docx")
    before = path.read_bytes()
    async with open_app([path]) as (app, pilot):
        editor = app.active_editor
        await chord(pilot, "ctrl+x", "ctrl+s")
        assert path.read_bytes() == before
        # Export to another file works and turns the buffer into that file.
        target = tmp_path / "report.md"
        app._save(editor, target)
        await pilot.pause()
        assert target.exists()
        assert "# Report title" in target.read_text()
        assert editor.path == target
        assert not editor.document_view
        assert path.read_bytes() == before


async def test_document_reconverts_when_changed_on_disk(tmp_path, builtin_only):
    path = write_docx(tmp_path / "report.docx")
    async with open_app([path]) as (app, pilot):
        editor = app.active_editor
        assert "The end." in editor.text
        # Rewrite the document with different content and an older-looking
        # mtime bump; the poll notices and re-converts.
        import os

        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("word/document.xml", DOCX_DOCUMENT.replace("The end.", "Fin."))
        stat = path.stat()
        os.utime(path, (stat.st_atime, stat.st_mtime + 5))
        assert await wait_for(pilot, lambda: "Fin." in editor.text)
        assert editor.read_only and editor.document_view


async def test_unreadable_document_falls_back_to_binary_view(tmp_path, builtin_only):
    broken = tmp_path / "broken.docx"
    broken.write_bytes(b"PK\x03\x04\x00\x00garbage")
    async with open_app([broken]) as (app, pilot):
        editor = app.active_editor
        assert not editor.document_view
        assert editor.binary
        assert editor.path == broken


# -- tab labels: same-named files get their directory --------------------------


def test_unique_labels_pure():
    a = Path("/proj/a/config.toml")
    b = Path("/proj/b/config.toml")
    c = Path("/proj/a/other.txt")
    assert unique_labels([a, c]) == ["config.toml", "other.txt"]
    assert unique_labels([a, b, c, None]) == [
        "a/config.toml",
        "b/config.toml",
        "other.txt",
        "*untitled*",
    ]
    # Needs two levels to tell apart.
    x = Path("/x/one/src/main.py")
    y = Path("/y/one/src/main.py")
    assert unique_labels([x, y]) == ["x/one/src/main.py", "y/one/src/main.py"]
    # Same path twice (two views) can't be distinguished and doesn't loop.
    assert unique_labels([a, a]) == ["proj/a/config.toml", "proj/a/config.toml"]


async def test_same_named_files_get_distinct_tabs(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    first = tmp_path / "a" / "notes.txt"
    second = tmp_path / "b" / "notes.txt"
    first.write_text("A\n")
    second.write_text("B\n")
    async with open_app([first]) as (app, pilot):
        labels = lambda: [tab.label_text for tab in app.tabs.query("Tab")]
        assert labels() == ["notes.txt"]
        await app._open_path(second)
        await pilot.pause()
        assert labels() == ["a/notes.txt", "b/notes.txt"]
        # Editing keeps the directory and adds the modified star.
        editor = app.active_editor
        editor.focus()
        await pilot.press("x")
        await pilot.pause()
        assert labels() == ["a/notes.txt", "b/notes.txt*"]
        # Killing one collapses the other back to the bare name.
        editor.text = "B\n"
        await pilot.pause()
        await chord(pilot, "ctrl+x", "k")
        assert await wait_for(pilot, lambda: labels() == ["notes.txt"])


# -- long operations show a busy indicator -------------------------------------


async def test_conversion_runs_off_thread_with_indicator(tmp_path, monkeypatch):
    """A slow conversion keeps the UI responsive and shows 'Converting…'."""
    import threading
    import time

    from candat import app as app_module

    path = write_docx(tmp_path / "slow.docx")
    release = threading.Event()

    def slow_convert(p):
        release.wait(5)
        return "# Converted\n"

    monkeypatch.setattr(app_module, "convert_to_markdown", slow_convert)
    async with open_app() as (app, pilot):
        status = app.query_one(StatusBar)
        app.run_worker(app._open_path(path), exclusive=False)
        assert await wait_for(pilot, lambda: "Converting slow.docx…" in status.busy_with)
        assert "⠋" in str(status.render()) or any(c in str(status.render()) for c in "⠙⠹⠸⠼⠴⠦⠧⠇⠏")
        # The UI is alive meanwhile: the tab exists and is labelled already.
        assert [t.label_text for t in app.tabs.query("Tab")] == ["slow.docx"]
        release.set()
        assert await wait_for(pilot, lambda: not status.busy_with)
        editor = app.active_editor
        assert editor.document_view and editor.text == "# Converted\n"
        assert "⠋" not in str(status.render())


async def test_preview_render_indicator_clears(tmp_path):
    path = tmp_path / "n.md"
    path.write_text("# Hi\n\n" + "para\n\n" * 200)
    async with open_app([path]) as (app, pilot):
        status = app.query_one(StatusBar)
        pane = app.active_pane
        assert await wait_for(pilot, lambda: "Hi" in pane.preview.plain_text())
        # Whether or not the indicator ever showed (renders are quick), it
        # must not be left behind once the render is done.
        assert await wait_for(pilot, lambda: not status.busy_with)


async def test_big_text_file_loads_without_highlighting(tmp_path):
    from candat.editor import HIGHLIGHT_MAX_BYTES

    path = tmp_path / "big.py"
    line = "x = 1  # " + "y" * 60 + "\n"
    path.write_text(line * (HIGHLIGHT_MAX_BYTES // len(line) + 10))
    async with open_app([path]) as (app, pilot):
        editor = app.active_editor
        assert editor.path == path
        assert editor.language is None  # too big to colour
        assert not editor.read_only  # still a normal, editable buffer
        assert not app.query_one(StatusBar).busy_with
