"""Word-processor documents (.docx / .odt) as markdown, for read-only viewing.

Opening one of these in candat converts it to markdown and shows the
rendered preview — the file itself is never loaded into the editor as text
(it's a zip of XML, i.e. "binary" to the editor) and never written back.

Conversion goes through pandoc when it is on PATH (it handles far more of
the formats than we care to), and otherwise falls back to the small
converter here: headings, paragraphs, bold/italic/strike, nested lists
(bullet and numbered), tables, links, line breaks and image placeholders,
built on nothing but `zipfile` and `xml.etree`.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path

DOC_SUFFIXES = {".docx", ".odt"}
PANDOC_TIMEOUT = 60


class ConversionError(Exception):
    """The file could not be turned into markdown (not a document, corrupt…)."""


def is_document(path: Path) -> bool:
    return path.suffix.lower() in DOC_SUFFIXES


def convert_to_markdown(path: Path) -> str:
    """Markdown for a .docx/.odt file. Raises ConversionError."""
    suffix = path.suffix.lower()
    if suffix not in DOC_SUFFIXES:
        raise ConversionError(f"not a supported document: {path.name}")
    if (pandoc := shutil.which("pandoc")) is not None:
        text = _pandoc(pandoc, path, suffix[1:])
        if text is not None:
            return text
    try:
        with zipfile.ZipFile(path) as archive:
            if suffix == ".docx":
                return DocxConverter(archive).convert()
            return OdtConverter(archive).convert()
    except (zipfile.BadZipFile, KeyError, ET.ParseError, OSError) as error:
        raise ConversionError(f"cannot read {path.name}: {error}") from error


def _pandoc(pandoc: str, path: Path, fmt: str) -> str | None:
    """pandoc's markdown, or None if pandoc failed (the caller falls back)."""
    try:
        result = subprocess.run(
            [pandoc, "-f", fmt, "-t", "gfm", "--wrap=none", str(path)],
            capture_output=True,
            timeout=PANDOC_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.decode("utf-8", "replace")


# -- shared markdown emitter -------------------------------------------------


@dataclass
class Span:
    text: str
    bold: bool = False
    italic: bool = False
    strike: bool = False
    href: str | None = None

    def style(self) -> tuple:
        return self.bold, self.italic, self.strike, self.href


@dataclass
class Block:
    kind: str  # "heading" | "para" | "item" | "table" | "quote" | "blank"
    spans: list[Span] | None = None
    level: int = 0  # heading level, or list nesting depth
    ordered: bool = False
    rows: list[list[str]] | None = None


def _inline(spans: list[Span]) -> str:
    """Spans → markdown, merging runs with identical styling so bold that
    Word split into several runs comes out as one `**…**`."""
    merged: list[Span] = []
    for span in spans:
        if not span.text:
            continue
        if merged and merged[-1].style() == span.style():
            merged[-1].text += span.text
        else:
            merged.append(Span(span.text, *span.style()))
    out: list[str] = []
    for span in merged:
        text = span.text
        if not (span.bold or span.italic or span.strike or span.href):
            out.append(text)
            continue
        # Emphasis markers must hug the text, so surrounding spaces move out.
        lead = text[: len(text) - len(text.lstrip())]
        trail = text[len(text.rstrip()) :]
        core = text.strip()
        if core:
            if span.strike:
                core = f"~~{core}~~"
            if span.bold and span.italic:
                core = f"***{core}***"
            elif span.bold:
                core = f"**{core}**"
            elif span.italic:
                core = f"*{core}*"
            if span.href:
                core = f"[{core}]({span.href})"
        out.append(lead + core + trail)
    return "".join(out)


def _table_markdown(rows: list[list[str]]) -> list[str]:
    if not rows:
        return []
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    cells = [[c.replace("|", "\\|").replace("\n", " ") for c in row] for row in rows]
    lines = ["| " + " | ".join(cells[0]) + " |", "|" + "---|" * width]
    lines += ["| " + " | ".join(row) + " |" for row in cells[1:]]
    return lines


def _emit(blocks: list[Block]) -> str:
    lines: list[str] = []
    counters: dict[int, int] = {}
    previous: Block | None = None
    for block in blocks:
        if block.kind != "item":
            counters.clear()
        if block.kind == "blank":
            continue
        # List items run together, except where a bullet list turns into a
        # numbered one (or back) at the same depth — that is a new list.
        need_gap = previous is not None and not (
            previous.kind == "item"
            and block.kind == "item"
            and (block.level > previous.level or block.ordered == previous.ordered)
        )
        if need_gap:
            lines.append("")
        if block.kind == "heading":
            lines.append("#" * max(1, min(6, block.level)) + " " + _inline(block.spans or []))
        elif block.kind == "para":
            lines.append(_inline(block.spans or []))
        elif block.kind == "quote":
            lines.append("> " + _inline(block.spans or []))
        elif block.kind == "item":
            for deeper in [d for d in counters if d > block.level]:
                del counters[deeper]
            indent = "  " * block.level
            if block.ordered:
                counters[block.level] = counters.get(block.level, 0) + 1
                marker = f"{counters[block.level]}."
            else:
                marker = "-"
            lines.append(f"{indent}{marker} {_inline(block.spans or [])}")
        elif block.kind == "table":
            lines.extend(_table_markdown(block.rows or []))
        previous = block
    text = "\n".join(lines).strip("\n")
    return text + "\n" if text else ""


# -- docx ---------------------------------------------------------------------

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
HEADING_RE = re.compile(r"^(?:heading|berschrift|titre|nadpis)\s*(\d)$", re.I)


class DocxConverter:
    def __init__(self, archive: zipfile.ZipFile) -> None:
        self.archive = archive
        self.rels = self._relationships()
        self.numbering = self._numbering()
        self.styles = self._styles()

    def _xml(self, name: str) -> ET.Element | None:
        try:
            return ET.fromstring(self.archive.read(name))
        except KeyError:
            return None

    def _relationships(self) -> dict[str, str]:
        root = self._xml("word/_rels/document.xml.rels")
        if root is None:
            return {}
        return {
            rel.get("Id", ""): rel.get("Target", "")
            for rel in root
            if rel.get("TargetMode") == "External"
        }

    def _numbering(self) -> dict[tuple[str, str], bool]:
        """(numId, ilvl) → ordered? from word/numbering.xml."""
        root = self._xml("word/numbering.xml")
        if root is None:
            return {}
        abstract: dict[str, dict[str, bool]] = {}
        for node in root.iter(f"{W}abstractNum"):
            levels: dict[str, bool] = {}
            for lvl in node.iter(f"{W}lvl"):
                fmt = lvl.find(f"{W}numFmt")
                value = fmt.get(f"{W}val", "bullet") if fmt is not None else "bullet"
                levels[lvl.get(f"{W}ilvl", "0")] = value != "bullet"
            abstract[node.get(f"{W}abstractNumId", "")] = levels
        table: dict[tuple[str, str], bool] = {}
        for num in root.iter(f"{W}num"):
            ref = num.find(f"{W}abstractNumId")
            if ref is None:
                continue
            for ilvl, ordered in abstract.get(ref.get(f"{W}val", ""), {}).items():
                table[(num.get(f"{W}numId", ""), ilvl)] = ordered
        return table

    def _styles(self) -> dict[str, str]:
        """style id → style name (Word localises names; ids are stable-ish)."""
        root = self._xml("word/styles.xml")
        if root is None:
            return {}
        out: dict[str, str] = {}
        for style in root.iter(f"{W}style"):
            name = style.find(f"{W}name")
            if name is not None:
                out[style.get(f"{W}styleId", "")] = name.get(f"{W}val", "")
        return out

    def convert(self) -> str:
        root = ET.fromstring(self.archive.read("word/document.xml"))
        body = root.find(f"{W}body")
        blocks: list[Block] = []
        if body is not None:
            self._blocks(body, blocks)
        return _emit(blocks)

    def _blocks(self, parent: ET.Element, blocks: list[Block]) -> None:
        for child in parent:
            if child.tag == f"{W}p":
                blocks.append(self._paragraph(child))
            elif child.tag == f"{W}tbl":
                blocks.append(Block("table", rows=self._table(child)))
            elif child.tag in (f"{W}sdt", f"{W}sdtContent", f"{W}ins"):
                self._blocks(child, blocks)  # content controls, tracked inserts

    def _paragraph(self, p: ET.Element) -> Block:
        spans: list[Span] = []
        self._runs(p, spans, None)
        props = p.find(f"{W}pPr")
        style_id = ""
        level = 0
        ordered = False
        is_item = False
        if props is not None:
            style = props.find(f"{W}pStyle")
            if style is not None:
                style_id = style.get(f"{W}val", "")
            num = props.find(f"{W}numPr")
            if num is not None:
                num_id = num.find(f"{W}numId")
                ilvl = num.find(f"{W}ilvl")
                num_val = num_id.get(f"{W}val", "") if num_id is not None else ""
                ilvl_val = ilvl.get(f"{W}val", "0") if ilvl is not None else "0"
                if num_val and num_val != "0":
                    is_item = True
                    level = int(ilvl_val) if ilvl_val.isdigit() else 0
                    ordered = self.numbering.get((num_val, ilvl_val), False)
            outline = props.find(f"{W}outlineLvl")
        else:
            outline = None
        if not spans or not "".join(s.text for s in spans).strip():
            return Block("blank")
        name = self.styles.get(style_id, style_id)
        for candidate in (style_id, name):
            if match := HEADING_RE.match(candidate.replace(" ", "")):
                return Block("heading", spans, level=int(match.group(1)))
        if style_id.lower() == "title" or name.lower() == "title":
            return Block("heading", spans, level=1)
        if outline is not None and (lvl := outline.get(f"{W}val", "")).isdigit():
            return Block("heading", spans, level=int(lvl) + 1)
        if is_item:
            return Block("item", spans, level=level, ordered=ordered)
        if _is_quote_style(style_id) or _is_quote_style(name):
            return Block("quote", spans)
        return Block("para", spans)

    def _runs(self, node: ET.Element, spans: list[Span], href: str | None) -> None:
        for child in node:
            tag = child.tag
            if tag == f"{W}r":
                spans.extend(self._run(child, href))
            elif tag == f"{W}hyperlink":
                target = self.rels.get(child.get(f"{R}id", ""))
                self._runs(child, spans, target or href)
            elif tag == f"{W}del":
                continue  # tracked deletion
            elif tag in (f"{W}pPr",):
                continue
            else:
                self._runs(child, spans, href)  # w:ins, w:smartTag, w:sdt …

    def _run(self, run: ET.Element, href: str | None) -> list[Span]:
        props = run.find(f"{W}rPr")
        bold = italic = strike = False
        if props is not None:
            bold = _on(props.find(f"{W}b"))
            italic = _on(props.find(f"{W}i"))
            strike = _on(props.find(f"{W}strike"))
        out: list[Span] = []
        for child in run:
            tag = child.tag
            if tag == f"{W}t":
                out.append(Span(child.text or "", bold, italic, strike, href))
            elif tag == f"{W}tab":
                out.append(Span("\t"))
            elif tag in (f"{W}br", f"{W}cr"):
                out.append(Span("  \n"))
            elif tag in (f"{W}drawing", f"{W}pict"):
                out.append(Span("![image]"))
            elif tag == f"{W}sym":
                out.append(Span(chr(int(child.get(f"{W}char", "20"), 16) & 0xFF)))
        return out

    def _table(self, tbl: ET.Element) -> list[list[str]]:
        rows: list[list[str]] = []
        for tr in tbl.findall(f"{W}tr"):
            row: list[str] = []
            for tc in tr.findall(f"{W}tc"):
                paragraphs = []
                for p in tc.iter(f"{W}p"):
                    spans: list[Span] = []
                    self._runs(p, spans, None)
                    paragraphs.append(_inline(spans).strip())
                row.append("<br>".join(p for p in paragraphs if p))
            rows.append(row)
        return rows


def _is_quote_style(name: str) -> bool:
    """Word's Quote / Intense Quote, pandoc's Block Text, LibreOffice's
    Quotations."""
    key = name.lower().replace(" ", "").replace("_20_", "")
    return "quote" in key or key == "blocktext" or key.startswith("quotation")


def _on(element: ET.Element | None) -> bool:
    """A toggle property (w:b, w:i…) is on when present unless val says off."""
    if element is None:
        return False
    return element.get(f"{W}val", "true").lower() not in ("0", "false", "off")


# -- odt ----------------------------------------------------------------------

TEXT = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
TABLE = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
OFFICE = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"
STYLE = "{urn:oasis:names:tc:opendocument:xmlns:style:1.0}"
FO = "{urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0}"
XLINK = "{http://www.w3.org/1999/xlink}"
DRAW = "{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}"


@dataclass
class OdtStyle:
    bold: bool = False
    italic: bool = False
    strike: bool = False
    parent: str | None = None
    family: str = ""


class OdtConverter:
    def __init__(self, archive: zipfile.ZipFile) -> None:
        self.archive = archive
        self.content = ET.fromstring(archive.read("content.xml"))
        try:
            styles_root: ET.Element | None = ET.fromstring(archive.read("styles.xml"))
        except KeyError:
            styles_root = None
        self.styles: dict[str, OdtStyle] = {}
        self.list_styles: dict[str, dict[int, bool]] = {}
        for root in (styles_root, self.content):
            if root is not None:
                self._collect_styles(root)

    def _collect_styles(self, root: ET.Element) -> None:
        for style in root.iter(f"{STYLE}style"):
            name = style.get(f"{STYLE}name", "")
            info = OdtStyle(
                parent=style.get(f"{STYLE}parent-style-name"),
                family=style.get(f"{STYLE}family", ""),
            )
            props = style.find(f"{STYLE}text-properties")
            if props is not None:
                info.bold = props.get(f"{FO}font-weight", "") in ("bold", "bolder") or (
                    props.get(f"{FO}font-weight", "").isdigit()
                    and int(props.get(f"{FO}font-weight", "0")) >= 600
                )
                info.italic = props.get(f"{FO}font-style", "") in ("italic", "oblique")
                info.strike = props.get(f"{STYLE}text-line-through-style", "none") not in (
                    "",
                    "none",
                )
            self.styles[name] = info
        for lst in root.iter(f"{TEXT}list-style"):
            levels: dict[int, bool] = {}
            for child in lst:
                lvl = child.get(f"{TEXT}level", "1")
                levels[int(lvl) if lvl.isdigit() else 1] = child.tag == f"{TEXT}list-level-style-number"
            self.list_styles[lst.get(f"{STYLE}name", "")] = levels

    def _chain(self, name: str | None) -> list[str]:
        """A style and its ancestors (automatic styles derive from named
        ones: P3 → Quotations → Standard)."""
        chain: list[str] = []
        while name and name not in chain:
            chain.append(name)
            info = self.styles.get(name)
            name = info.parent if info is not None else None
        return chain

    def _resolved(self, name: str | None) -> tuple[bool, bool, bool]:
        bold = italic = strike = False
        for style_name in self._chain(name):
            info = self.styles.get(style_name)
            if info is None:
                continue
            bold = bold or info.bold
            italic = italic or info.italic
            strike = strike or info.strike
        return bold, italic, strike

    def convert(self) -> str:
        body = self.content.find(f"{OFFICE}body")
        text = body.find(f"{OFFICE}text") if body is not None else None
        blocks: list[Block] = []
        if text is not None:
            self._blocks(text, blocks)
        return _emit(blocks)

    def _blocks(self, parent: ET.Element, blocks: list[Block]) -> None:
        for child in parent:
            tag = child.tag
            if tag == f"{TEXT}h":
                level = child.get(f"{TEXT}outline-level", "1")
                spans = self._spans(child)
                if _has_text(spans):
                    blocks.append(Block("heading", spans, level=int(level) if level.isdigit() else 1))
            elif tag == f"{TEXT}p":
                spans = self._spans(child)
                if _has_text(spans):
                    chain = self._chain(child.get(f"{TEXT}style-name"))
                    if any(_is_quote_style(name) for name in chain):
                        blocks.append(Block("quote", spans))
                    else:
                        blocks.append(Block("para", spans))
            elif tag == f"{TEXT}list":
                self._list(child, blocks, 0, None)
            elif tag == f"{TABLE}table":
                blocks.append(Block("table", rows=self._table(child)))
            elif tag in (f"{TEXT}section", f"{OFFICE}text"):
                self._blocks(child, blocks)

    def _list(
        self,
        lst: ET.Element,
        blocks: list[Block],
        depth: int,
        style_name: str | None,
    ) -> None:
        style_name = lst.get(f"{TEXT}style-name") or style_name
        ordered = self.list_styles.get(style_name or "", {}).get(depth + 1, False)
        for item in lst:
            if item.tag not in (f"{TEXT}list-item", f"{TEXT}list-header"):
                continue
            first = True
            for child in item:
                if child.tag in (f"{TEXT}p", f"{TEXT}h"):
                    spans = self._spans(child)
                    if not _has_text(spans):
                        continue
                    if first:
                        blocks.append(Block("item", spans, level=depth, ordered=ordered))
                        first = False
                    else:  # continuation paragraph inside the item
                        blocks[-1].spans = (blocks[-1].spans or []) + [Span("  \n")] + spans
                elif child.tag == f"{TEXT}list":
                    self._list(child, blocks, depth + 1, style_name)

    def _spans(self, node: ET.Element, href: str | None = None) -> list[Span]:
        out: list[Span] = []
        self._walk(node, out, href, (False, False, False))
        return out

    def _walk(
        self,
        node: ET.Element,
        out: list[Span],
        href: str | None,
        inherited: tuple[bool, bool, bool],
    ) -> None:
        if node.text:
            out.append(Span(node.text, *inherited, href))
        for child in node:
            tag = child.tag
            if tag == f"{TEXT}span":
                own = self._resolved(child.get(f"{TEXT}style-name"))
                merged = tuple(a or b for a, b in zip(inherited, own))
                self._walk(child, out, href, merged)  # type: ignore[arg-type]
            elif tag == f"{TEXT}a":
                self._walk(child, out, child.get(f"{XLINK}href") or href, inherited)
            elif tag == f"{TEXT}s":
                count = child.get(f"{TEXT}c", "1")
                out.append(Span(" " * (int(count) if count.isdigit() else 1)))
            elif tag == f"{TEXT}tab":
                out.append(Span("\t"))
            elif tag == f"{TEXT}line-break":
                out.append(Span("  \n"))
            elif tag == f"{TEXT}note":
                body = child.find(f"{TEXT}note-body")
                if body is not None:
                    inner: list[Span] = []
                    for p in body.iter(f"{TEXT}p"):
                        self._walk(p, inner, None, (False, False, False))
                    out.append(Span(" (" + _inline(inner).strip() + ")"))
            elif tag == f"{DRAW}frame":
                if child.find(f"{DRAW}image") is not None:
                    out.append(Span("![image]"))
                for p in child.iter(f"{TEXT}p"):  # text boxes
                    self._walk(p, out, href, inherited)
            elif tag in (f"{TEXT}soft-page-break", f"{TEXT}bookmark", f"{TEXT}bookmark-start", f"{TEXT}bookmark-end", f"{TEXT}tracked-changes"):
                pass
            else:
                self._walk(child, out, href, inherited)
            if child.tail:
                out.append(Span(child.tail, *inherited, href))

    def _table(self, table: ET.Element) -> list[list[str]]:
        rows: list[list[str]] = []
        for tr in table.iter(f"{TABLE}table-row"):
            row: list[str] = []
            for tc in tr:
                if tc.tag not in (f"{TABLE}table-cell", f"{TABLE}covered-table-cell"):
                    continue
                paragraphs = [
                    _inline(self._spans(p)).strip() for p in tc.iter(f"{TEXT}p")
                ]
                row.append("<br>".join(p for p in paragraphs if p))
            rows.append(row)
        return rows


def _has_text(spans: list[Span]) -> bool:
    return any(span.text.strip() for span in spans)
