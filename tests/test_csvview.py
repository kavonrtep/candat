"""Tests for the CSV/TSV table viewer."""

import os
from pathlib import Path

import pytest
from textual.widgets import Input

from candat.app import CandatApp
from candat.csvview import INITIAL_ROWS, CsvViewer, sniff_dialect

pytestmark = pytest.mark.asyncio


async def chord(pilot, *keys):
    for key in keys:
        await pilot.press(key)
        await pilot.pause()
    await pilot.pause()


def write_csv(path: Path, rows: int) -> None:
    lines = ["id,name,value"]
    lines += [f"{i},item{i},{i * 10}" for i in range(1, rows + 1)]
    path.write_text("\n".join(lines) + "\n")


async def open_viewer(app, pilot) -> CsvViewer:
    await pilot.pause()
    await pilot.pause()
    pane = app.tabs.active_pane
    assert pane.has_class("-csv-table")
    return pane.query_one(CsvViewer)


async def test_csv_opens_in_table_mode_without_loading_text(tmp_path: Path):
    f = tmp_path / "data.csv"
    write_csv(f, 50)
    app = CandatApp([f])
    async with app.run_test() as pilot:
        viewer = await open_viewer(app, pilot)
        table = viewer.table
        assert [str(c.label) for c in table.columns.values()] == ["id", "name", "value"]
        assert table.row_count == 50
        assert list(table.get_row_at(0)) == ["1", "item1", "10"]
        # The text buffer stays empty: no giant file was loaded.
        assert app.active_editor.text == ""
        assert app.focused is table


async def test_large_file_loads_incrementally(tmp_path: Path):
    f = tmp_path / "big.csv"
    write_csv(f, 5000)
    app = CandatApp([f])
    async with app.run_test() as pilot:
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


async def test_search_streams_beyond_loaded_rows(tmp_path: Path):
    f = tmp_path / "big.csv"
    write_csv(f, 3000)
    app = CandatApp([f])
    async with app.run_test() as pilot:
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


async def test_filter_rows(tmp_path: Path):
    f = tmp_path / "data.csv"
    write_csv(f, 300)
    app = CandatApp([f])
    async with app.run_test() as pilot:
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


async def test_toggle_to_text_and_back(tmp_path: Path):
    f = tmp_path / "small.csv"
    write_csv(f, 5)
    app = CandatApp([f])
    async with app.run_test() as pilot:
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


async def test_save_guard_in_table_mode(tmp_path: Path):
    f = tmp_path / "keep.csv"
    write_csv(f, 5)
    original = f.read_text()
    app = CandatApp([f])
    async with app.run_test() as pilot:
        await open_viewer(app, pilot)
        await chord(pilot, "ctrl+x", "ctrl+s")
        # The empty (never-loaded) text buffer must NOT clobber the file.
        assert f.read_text() == original


async def test_autoreload_in_table_mode(tmp_path: Path):
    f = tmp_path / "grow.csv"
    write_csv(f, 10)
    app = CandatApp([f])
    async with app.run_test() as pilot:
        viewer = await open_viewer(app, pilot)
        assert viewer.table.row_count == 10
        old = f.stat().st_mtime
        write_csv(f, 20)
        os.utime(f, (old + 5, old + 5))
        app._check_disk_changes()
        await pilot.pause()
        assert viewer.table.row_count == 20


async def test_tsv_delimiter(tmp_path: Path):
    f = tmp_path / "data.tsv"
    f.write_text("a\tb\n1\t2\n")
    assert sniff_dialect(f)[0] == "\t"
    app = CandatApp([f])
    async with app.run_test() as pilot:
        viewer = await open_viewer(app, pilot)
        assert list(viewer.table.get_row_at(0)) == ["1", "2"]
