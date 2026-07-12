"""Tests for the CSV/TSV table viewer."""

import os

import pytest
from textual.widgets import Input

from candat.csvview import INITIAL_ROWS, CsvViewer, sniff_dialect
from helpers import chord, open_app, write_csv

pytestmark = pytest.mark.asyncio


async def open_viewer(app, pilot) -> CsvViewer:
    await pilot.pause()
    await pilot.pause()
    pane = app.tabs.active_pane
    assert pane.has_class("-csv-table")
    return pane.csv


async def test_csv_opens_in_table_mode_without_loading_text(sample_csv):
    async with open_app([sample_csv]) as (app, pilot):
        viewer = await open_viewer(app, pilot)
        table = viewer.table
        assert [str(c.label) for c in table.columns.values()] == ["id", "name", "value"]
        assert table.row_count == 50
        assert [str(c) for c in table.get_row_at(0)] == ["1", "item1", "10"]
        # The text buffer stays empty: no giant file was loaded.
        assert app.active_editor.text == ""
        assert app.focused is table


async def test_large_file_loads_incrementally(make_csv):
    async with open_app([make_csv(5000, "big.csv")]) as (app, pilot):
        viewer = await open_viewer(app, pilot)
        table = viewer.table
        assert table.row_count == INITIAL_ROWS  # not the whole file
        # Moving the cursor near the end streams in another batch.
        table.move_cursor(row=table.row_count - 1)
        await pilot.pause()
        assert table.row_count > INITIAL_ROWS
        # G loads everything and jumps to the last row.
        await pilot.press("G")
        await pilot.pause()
        assert table.row_count == 5000
        assert table.cursor_row == 4999
        # Row labels keep original file line numbers (header = line 1).
        assert str(table.get_row_at(4999)[0]) == "5000"


async def test_search_streams_beyond_loaded_rows(make_csv):
    async with open_app([make_csv(3000, "big.csv")]) as (app, pilot):
        viewer = await open_viewer(app, pilot)
        await pilot.press("slash")
        await pilot.pause()
        prompt = app.screen.query_one(Input)
        prompt.value = "item2500"
        await pilot.press("enter")
        await pilot.pause()
        table = viewer.table
        assert "item2500" in [str(c) for c in table.get_row_at(table.cursor_row)]
        # n repeats: no further match -> stays put (2500 unique).
        row_before = table.cursor_row
        await pilot.press("n")
        await pilot.pause()
        assert table.cursor_row == row_before


async def test_search_backward_with_ctrl_r(tmp_path):
    # A marker that lands on a few known rows (item3, item13, item23, item33).
    path = tmp_path / "mark.csv"
    path.write_text(
        "id,name,value\n"
        + "".join(f"{i},item{i},{i}\n" for i in range(1, 41))
    )
    async with open_app([path]) as (app, pilot):
        viewer = await open_viewer(app, pilot)
        table = viewer.table

        def name(row):
            return str(table.get_row_at(row)[1])

        await pilot.press("slash")
        await pilot.pause()
        app.screen.query_one(Input).value = "item3"  # item3, item30..item39
        await pilot.press("enter")
        await pilot.pause()
        first = table.cursor_row
        assert name(first) == "item3"

        await pilot.press("ctrl+s")  # next match forward
        await pilot.pause()
        second = table.cursor_row
        assert second > first and name(second) == "item30"

        await pilot.press("ctrl+r")  # previous match — the bug: this did nothing
        await pilot.pause()
        assert table.cursor_row == first and name(table.cursor_row) == "item3"

        # N is the same as C-r; at the top it reports no earlier match and stays.
        await pilot.press("N")
        await pilot.pause()
        assert table.cursor_row == first


async def test_search_highlights_cells_and_cancel_keeps_position(sample_csv):
    from rich.text import Text

    from candat.csvview import HIGHLIGHT

    async with open_app([sample_csv]) as (app, pilot):
        viewer = await open_viewer(app, pilot)
        table = viewer.table

        def highlighted_cells() -> int:
            return sum(
                1
                for r in range(table.row_count)
                for cell in table.get_row_at(r)
                if isinstance(cell, Text)
                and any(s.style == HIGHLIGHT for s in cell.spans)
            )

        assert highlighted_cells() == 0
        viewer.search("item")  # every name cell contains 'item'
        await pilot.pause()
        assert viewer.searching and highlighted_cells() == 50
        pos = table.cursor_row
        viewer.cancel_search()  # C-g / Esc
        await pilot.pause()
        assert not viewer.searching
        assert highlighted_cells() == 0
        assert table.cursor_row == pos  # in-place restyle keeps the cursor


async def test_search_is_literal_smart_case(make_csv):
    """Table search speaks the same dialect as the editor and pager: literal
    text, smart case — regex metacharacters are matched verbatim."""
    async with open_app([make_csv(100, "lit.csv")]) as (app, pilot):
        viewer = await open_viewer(app, pilot)
        table = viewer.table
        row_before = table.cursor_row
        viewer.search("item1.")  # a literal dot: matches nothing
        await pilot.pause()
        assert table.cursor_row == row_before
        viewer.cancel_search()
        viewer.search("ITEM42")  # uppercase -> exact case: no match
        await pilot.pause()
        assert table.cursor_row == row_before
        viewer.cancel_search()
        viewer.search("item42")  # lowercase -> case-insensitive: hit
        await pilot.pause()
        assert "item42" in [str(c) for c in table.get_row_at(table.cursor_row)]


async def test_cells_stay_plain_strings_without_search(make_csv):
    """Cells are only promoted to Rich Text when they match an active search;
    a plain load (and non-matching cells during a search) stays str, which is
    what keeps a 200k-row table cheap."""
    from rich.text import Text

    async with open_app([make_csv(100, "plain.csv")]) as (app, pilot):
        viewer = await open_viewer(app, pilot)
        table = viewer.table
        assert not any(
            isinstance(cell, Text)
            for r in range(table.row_count)
            for cell in table.get_row_at(r)
        )
        viewer.search("item42")  # exactly one matching cell
        await pilot.pause()
        texts = [
            cell
            for r in range(table.row_count)
            for cell in table.get_row_at(r)
            if isinstance(cell, Text)
        ]
        assert len(texts) == 1 and texts[0].plain == "item42"
        # rows streamed in later are styled as they load
        viewer.load_all()
        viewer.cancel_search()
        assert not any(
            isinstance(cell, Text)
            for r in range(table.row_count)
            for cell in table.get_row_at(r)
        )


async def test_filter_rows(make_csv):
    async with open_app([make_csv(300)]) as (app, pilot):
        viewer = await open_viewer(app, pilot)
        await pilot.press("ampersand")
        await pilot.pause()
        prompt = app.screen.query_one(Input)
        prompt.value = "item29\\d"
        await pilot.press("enter")
        await pilot.pause()
        table = viewer.table
        assert table.row_count == 10  # item290..item299
        # Original line numbers preserved as labels.
        assert str(table.get_row_at(0)[0]) == "290"
        # Empty pattern clears the filter.
        await pilot.press("ampersand")
        await pilot.pause()
        app.screen.query_one(Input).value = ""
        await pilot.press("enter")
        await pilot.pause()
        assert viewer.table.row_count == 300


async def test_toggle_to_text_and_back(make_csv):
    async with open_app([make_csv(5, "small.csv")]) as (app, pilot):
        viewer = await open_viewer(app, pilot)
        pane = app.tabs.active_pane
        await chord(pilot, "ctrl+c", "ctrl+v")  # -> text mode
        assert not pane.has_class("-csv-table")
        editor = app.active_editor
        assert editor.text.startswith("id,name,value")
        assert app.focused is editor
        await chord(pilot, "ctrl+c", "ctrl+v")  # -> back to table
        assert pane.has_class("-csv-table")
        assert app.focused is viewer.table


async def test_save_guard_in_table_mode(make_csv):
    path = make_csv(5, "keep.csv")
    original = path.read_text()
    async with open_app([path]) as (app, pilot):
        await open_viewer(app, pilot)
        await chord(pilot, "ctrl+x", "ctrl+s")
        # The empty (never-loaded) text buffer must NOT clobber the file.
        assert path.read_text() == original


async def test_autoreload_in_table_mode(make_csv):
    path = make_csv(10, "grow.csv")
    async with open_app([path]) as (app, pilot):
        viewer = await open_viewer(app, pilot)
        assert viewer.table.row_count == 10
        old = path.stat().st_mtime
        write_csv(path, 20)
        os.utime(path, (old + 5, old + 5))
        app._check_disk_changes()
        await pilot.pause()
        assert viewer.table.row_count == 20


async def test_any_buffer_toggles_to_table(tmp_path):
    """C-c C-v turns a non-.csv file (tab-delimited .txt) into a table."""
    path = tmp_path / "genes.txt"
    path.write_text("chrom\tstart\tend\nchr1\t100\t200\nchr2\t300\t400\n")
    async with open_app([path]) as (app, pilot):
        pane = app.tabs.active_pane
        assert not pane.has_class("-csv-table")  # .txt opens in the editor
        await chord(pilot, "ctrl+c", "ctrl+v")
        assert pane.has_class("-csv-table")
        table = pane.csv.table
        assert [str(c.label) for c in table.columns.values()] == ["chrom", "start", "end"]
        assert [str(c) for c in table.get_row_at(0)] == ["chr1", "100", "200"]
        await chord(pilot, "ctrl+c", "ctrl+v")  # and back to text
        assert not pane.has_class("-csv-table")
        assert app.active_editor.text.startswith("chrom\t")


async def test_delimiter_picker_reparses(tmp_path):
    """`d` in the table re-parses with the chosen delimiter."""
    path = tmp_path / "odd.txt"
    path.write_text("a:b:c\n1:2:3\n4:5:6\n")  # ':' is not in the sniffer set
    async with open_app([path]) as (app, pilot):
        pane = app.tabs.active_pane
        await chord(pilot, "ctrl+c", "ctrl+v")
        viewer = pane.csv
        assert len(viewer._columns) == 1  # sniff fell back to comma: one column
        await pilot.press("d")
        await pilot.pause()
        app.screen.query_one(Input).value = ":"
        await pilot.press("enter")
        await pilot.pause()
        assert len(viewer._columns) == 3
        assert [str(c) for c in viewer.table.get_row_at(0)] == ["1", "2", "3"]
        # named aliases work too
        viewer.set_delimiter("\t")
        assert len(viewer._columns) == 1  # no tabs in this file
        from candat.csvview import parse_delimiter

        assert parse_delimiter("tab") == "\t"
        assert parse_delimiter("space") == " "
        assert parse_delimiter("pipe") == "|"
        assert parse_delimiter("too long") is None


async def test_modified_buffer_tables_from_text(tmp_path):
    """A buffer with unsaved edits is parsed from the buffer text, not the
    stale file on disk."""
    path = tmp_path / "data.txt"
    path.write_text("x;y\n1;2\n")
    async with open_app([path]) as (app, pilot):
        editor = app.active_editor
        editor.text = "x;y\n1;2\n3;4\n"  # unsaved extra row
        editor.modified = True
        await chord(pilot, "ctrl+c", "ctrl+v")
        pane = app.tabs.active_pane
        assert pane.has_class("-csv-table")
        assert pane.csv.table.row_count == 2  # both data rows, from the buffer


async def test_pager_and_table_round_trip_on_large_file(tmp_path):
    """A large delimited file opens in the pager; C-c C-v switches to the
    (streaming) table view, and C-c C-v again returns to the pager — never
    loading the whole file into the editor."""
    big = tmp_path / "huge_regions.txt"
    line = "chr1\t%d\t%d\tfeature%d\n"
    with big.open("w") as f:
        for i in range(450_000):  # ~13 MB, over the pager threshold
            f.write(line % (i, i + 100, i))
    async with open_app([big]) as (app, pilot):
        pane = app.tabs.active_pane
        await pilot.pause()
        assert pane.is_pager  # routed to the pager by size
        await chord(pilot, "ctrl+c", "ctrl+v")  # pager -> table
        assert not pane.is_pager and pane.has_class("-csv-table")
        table = pane.csv.table
        assert str(table.get_row_at(0)[0]) == "chr1"
        assert len(pane.csv._columns) == 4  # tab-sniffed
        assert app.active_editor.text == ""  # editor never loaded the file
        await chord(pilot, "ctrl+c", "ctrl+v")  # table -> back to the pager
        assert pane.is_pager and not pane.has_class("-csv-table")
        assert app.active_editor.text == ""


async def test_table_suffixes_config_routes_on_open(tmp_path):
    from candat import config

    cfg = config.config_path()
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text('table_suffixes = [".csv", ".tsv", ".bed"]\n')
    path = tmp_path / "regions.bed"
    path.write_text("chr1\t100\t200\nchr1\t500\t900\n")
    async with open_app([path]) as (app, pilot):
        pane = app.tabs.active_pane
        await pilot.pause()
        assert pane.has_class("-csv-table")  # .bed now opens as a table


async def test_tsv_delimiter(sample_tsv):
    assert sniff_dialect(sample_tsv)[0] == "\t"
    async with open_app([sample_tsv]) as (app, pilot):
        viewer = await open_viewer(app, pilot)
        assert [str(c) for c in viewer.table.get_row_at(0)] == ["1", "2", "3"]
