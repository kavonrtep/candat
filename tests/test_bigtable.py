"""Tests for the windowed unlimited-row table view (BigTable)."""

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Input

from candat.bigtable import BigTable
from helpers import chord, open_app, wait_for

pytestmark = pytest.mark.asyncio


def make_table_file(tmp_path, rows=300_000, name="big.tsv"):
    """A tab-delimited file big enough (> 200k rows) to defeat the classic
    capped table."""
    path = tmp_path / name
    with path.open("w") as f:
        f.write("chrom\tstart\tend\tname\n")
        for i in range(rows):
            f.write(f"chr{i % 22 + 1}\t{i}\t{i + 100}\tfeature{i}\n")
    return path


class TableApp(App):
    def __init__(self, path):
        super().__init__()
        self._path = path

    def compose(self) -> ComposeResult:
        yield BigTable(id="t")


async def open_table(path, size=(80, 16)):
    app = TableApp(path)
    cm = app.run_test(size=size)
    pilot = await cm.__aenter__()
    table = app.query_one(BigTable)
    table.load(path)
    await wait_for(pilot, lambda: not table._indexing)
    return app, pilot, cm, table


async def do_search(app, pilot, table, term):
    table.search(term)
    await app.workers.wait_for_complete()
    await pilot.pause()


async def test_unlimited_rows_and_random_access(tmp_path):
    path = make_table_file(tmp_path)
    app, pilot, cm, table = await open_table(path)
    try:
        assert table.total_rows == 300_000  # far beyond the classic cap
        assert table._columns == ["chrom", "start", "end", "name"]
        assert table._delimiter == "\t"
        # Random access anywhere costs one seek, not a quarter-million loads.
        assert table.fetch_rows(250_000, 1) == [
            ["chr" + str(250_000 % 22 + 1), "250000", "250100", "feature250000"]
        ]
        table.action_goto_bottom()
        assert table.cursor_row == 299_999
        assert table.fetch_rows(table.cursor_row, 1)[0][3] == "feature299999"
        table.action_goto_top()
        assert table.cursor_row == 0
    finally:
        await cm.__aexit__(None, None, None)


async def test_search_collects_all_matches_deep(tmp_path):
    path = make_table_file(tmp_path)
    app, pilot, cm, table = await open_table(path)
    try:
        # 'feature25xxxx' rows live far beyond the old 200k cap.
        await do_search(app, pilot, table, "feature2999")  # 2999, 29990..29999, 299900..
        assert table._matches  # all matches collected by one scan
        assert max(table._matches) > 200_000
        # n / N are instant bisects over the collected match list.
        table.action_goto_top()
        table.step_match(True)
        first = table.cursor_row
        assert first == table._matches[0]
        table.step_match(False)  # wraps to the last match
        assert table.cursor_row == table._matches[-1]
        # A miss resets the query, same contract as pager/editor.
        await do_search(app, pilot, table, "no-such-thing-zzz")
        assert not table.searching
    finally:
        await cm.__aexit__(None, None, None)


async def test_render_streams_viewport_only(tmp_path):
    path = make_table_file(tmp_path)
    app, pilot, cm, table = await open_table(path)
    try:
        table.goto_row(123_456)
        text = table.render().plain
        assert "feature123456" in text
        assert "row 123,457/300,000" in text  # 1-based in the status line
        # header stays sticky at the top
        assert text.splitlines()[0].lstrip("# ").startswith("chrom")
    finally:
        await cm.__aexit__(None, None, None)


async def test_delimiter_switch_reuses_index(tmp_path):
    path = tmp_path / "colon.txt"
    with path.open("w") as f:
        for i in range(250_000):
            f.write(f"a{i}:b{i}:c{i}\n")
    app, pilot, cm, table = await open_table(path)
    try:
        assert table.total_rows + table._header_offset == 250_000
        assert len(table._columns) == 1  # ':' isn't sniffed
        table.set_delimiter(":")
        table.render()
        assert len(table._columns) == 3  # re-parsed, no re-index needed
    finally:
        await cm.__aexit__(None, None, None)


async def do_filter(app, pilot, table, pattern):
    table.apply_filter(pattern)
    await app.workers.wait_for_complete()
    await pilot.pause()


async def test_filter_restricts_view_keeps_line_numbers(tmp_path):
    path = make_table_file(tmp_path)
    app, pilot, cm, table = await open_table(path)
    try:
        await do_filter(app, pilot, table, r"chr7\t")  # every 22nd row
        assert table.filtered
        expected = len([i for i in range(300_000) if i % 22 + 1 == 7])
        assert table.view_count() == expected
        # Navigation walks only filtered rows; gutter keeps original lines.
        table.action_goto_top()
        first = table.cursor_row
        assert table.fetch_rows(first, 1)[0][0] == "chr7"
        table.action_cursor(1)
        second = table.cursor_row
        assert second - first == 22  # next chr7 row, 22 data rows later
        rendered = table.render().plain
        assert f"row 2/{expected:,}" in rendered
        assert "filter: chr7" in rendered
        # G goes to the last *filtered* row.
        table.action_goto_bottom()
        assert table.fetch_rows(table.cursor_row, 1)[0][0] == "chr7"
        # Empty pattern clears the filter.
        await do_filter(app, pilot, table, "")
        assert not table.filtered and table.view_count() == 300_000
    finally:
        await cm.__aexit__(None, None, None)


async def test_search_intersects_with_filter(tmp_path):
    path = make_table_file(tmp_path)
    app, pilot, cm, table = await open_table(path)
    try:
        await do_filter(app, pilot, table, r"chr7\t")
        await do_search(app, pilot, table, "feature10")  # many rows, all chroms
        assert table._matches  # effective matches
        assert len(table._matches) < len(table._all_matches)
        # every effective match is a chr7 row
        for row in list(table._matches)[:20]:
            assert table.fetch_rows(row, 1)[0][0] == "chr7"
        # n steps stay inside the filter
        table.step_match(True)
        assert table.fetch_rows(table.cursor_row, 1)[0][0] == "chr7"
        # clearing the filter widens matches back out
        await do_filter(app, pilot, table, "")
        assert len(table._matches) == len(table._all_matches)
    finally:
        await cm.__aexit__(None, None, None)


async def test_filter_bad_regex_and_no_rows(tmp_path):
    path = make_table_file(tmp_path, rows=1000)
    app, pilot, cm, table = await open_table(path)
    try:
        table.apply_filter("[unclosed")
        assert not table.filtered  # rejected, view unchanged
        await do_filter(app, pilot, table, "nothing-matches-zzz")
        assert table.filtered and table.view_count() == 0
        table.render()  # empty filtered view renders without crashing
        await do_filter(app, pilot, table, "")
    finally:
        await cm.__aexit__(None, None, None)


async def test_check_disk_growth_and_truncation(tmp_path):
    path = make_table_file(tmp_path, rows=5000)
    app, pilot, cm, table = await open_table(path)
    try:
        assert table.total_rows == 5000
        table.goto_row(100)
        with path.open("a") as f:
            for i in range(5000, 5100):
                f.write(f"chr1\t{i}\t{i + 100}\tfeature{i}\n")
        assert table.check_disk() == "grew"
        assert table.total_rows == 5100
        assert table.cursor_row == 100  # viewport stayed put
        assert table.fetch_rows(5099, 1)[0][3] == "feature5099"
        import os

        os.truncate(path, 100)
        assert table.check_disk() == "reloaded"
        await wait_for(pilot, lambda: not table._indexing)
        assert table.total_rows < 5000
    finally:
        await cm.__aexit__(None, None, None)


async def test_big_file_routes_to_bigtable_on_open(tmp_path):
    path = make_table_file(tmp_path, name="big.csv")  # .csv auto-opens as table
    async with open_app([path]) as (app, pilot):
        pane = app.tabs.active_pane
        await pilot.pause()
        assert pane.is_bigtable  # windowed, not the capped classic table
        assert not pane.has_class("-csv-table")
        table = pane.bigtable
        await wait_for(pilot, lambda: not table._indexing)
        assert table.total_rows == 300_000
        assert app.focused is table


async def test_pager_bigtable_round_trip(tmp_path):
    # 450k rows -> ~12 MB, over the pager threshold; .txt is not a table suffix.
    path = make_table_file(tmp_path, rows=450_000, name="big.txt")
    async with open_app([path]) as (app, pilot):
        pane = app.tabs.active_pane
        await pilot.pause()
        assert pane.is_pager  # routed to the pager by size
        await chord(pilot, "ctrl+c", "ctrl+v")  # pager -> windowed table
        assert pane.is_bigtable and not pane.is_pager
        await wait_for(pilot, lambda: not pane.bigtable._indexing)
        assert pane.bigtable.total_rows == 450_000
        assert app.active_editor.text == ""  # never loaded into the editor
        await chord(pilot, "ctrl+c", "ctrl+v")  # table -> back to the pager
        assert pane.is_pager and not pane.is_bigtable


async def test_quoted_newlines_stay_on_classic_table(tmp_path):
    path = tmp_path / "quoted.csv"
    with path.open("w") as f:
        f.write('id,note\n1,"line one\nline two"\n2,plain\n')
    async with open_app([path]) as (app, pilot):
        pane = app.tabs.active_pane
        await pilot.pause()
        # Line-based indexing would mis-split this; the classic parser is exact.
        assert pane.has_class("-csv-table") and not pane.is_bigtable
        assert pane.csv.table.row_count == 2
