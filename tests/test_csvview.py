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
    return pane.query_one(CsvViewer)


async def test_csv_opens_in_table_mode_without_loading_text(sample_csv):
    async with open_app([sample_csv]) as (app, pilot):
        viewer = await open_viewer(app, pilot)
        table = viewer.table
        assert [str(c.label) for c in table.columns.values()] == ["id", "name", "value"]
        assert table.row_count == 50
        assert list(table.get_row_at(0)) == ["1", "item1", "10"]
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


async def test_tsv_delimiter(sample_tsv):
    assert sniff_dialect(sample_tsv)[0] == "\t"
    async with open_app([sample_tsv]) as (app, pilot):
        viewer = await open_viewer(app, pilot)
        assert list(viewer.table.get_row_at(0)) == ["1", "2", "3"]
