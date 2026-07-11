"""Tests for the windowed large-file pager (stage 1)."""

import pytest
from textual.app import App, ComposeResult

from candat.pager import TextPager

pytestmark = pytest.mark.asyncio


def make_file(tmp_path, n=100_000, long_at=10):
    f = tmp_path / "big.txt"
    lines = [f"line {i}" for i in range(n)]
    if long_at is not None:
        lines[long_at] = "L" + "x" * 250
    f.write_text("\n".join(lines) + "\n")
    return f


class PagerApp(App):
    def __init__(self, path):
        super().__init__()
        self._path = path

    def compose(self) -> ComposeResult:
        yield TextPager(self._path, id="p")


async def open_pager(path, size=(40, 12)):
    app = PagerApp(path)
    cm = app.run_test(size=size)
    pilot = await cm.__aenter__()
    pager = app.query_one(TextPager)
    for _ in range(60):
        await pilot.pause()
        if not pager._indexing:
            break
    return app, pilot, cm, pager


async def test_index_and_random_read(tmp_path):
    app, pilot, cm, pager = await open_pager(make_file(tmp_path))
    try:
        assert pager.line_count == 100_000
        assert pager.read_line(0) == "line 0"
        assert pager.read_line(50_000) == "line 50000"
        assert pager.read_line(99_999) == "line 99999"
    finally:
        await cm.__aexit__(None, None, None)


async def test_scroll_and_jump(tmp_path):
    app, pilot, cm, pager = await open_pager(make_file(tmp_path))
    try:
        assert pager._viewport_rows(3, 40) == ["line 0", "line 1", "line 2"]
        pager.action_scroll_lines(3)
        assert pager.top_line == 3
        pager.action_scroll_page(1)
        assert pager.top_line > 3
        pager.action_goto_end()
        assert pager.top_line >= 99_000  # last line parked at the bottom
        pager.action_goto_start()
        assert pager.top_line == 0 and pager.top_seg == 0
    finally:
        await cm.__aexit__(None, None, None)


async def test_wrap_splits_long_line(tmp_path):
    app, pilot, cm, pager = await open_pager(make_file(tmp_path, long_at=10))
    try:
        pager.goto_line(10)
        assert not pager.wrap
        # no-wrap: the long line is a single (truncated) row
        assert len(pager._viewport_rows(1, 40)) == 1
        # wrap: it spans several width-40 segments
        assert pager.toggle_wrap() is True
        rows = pager._viewport_rows(6, 40)
        assert all(len(r) <= 40 for r in rows)
        assert len(rows[0]) == 40 and len(rows[1]) == 40  # 251 chars -> multiple rows
    finally:
        await cm.__aexit__(None, None, None)


async def test_horizontal_scroll_no_wrap(tmp_path):
    app, pilot, cm, pager = await open_pager(make_file(tmp_path, long_at=0))
    try:
        pager.goto_line(0)
        pager.action_scroll_h(8)
        assert pager.hoffset == 8
        # the row starts 8 chars into the long line (render() crops to width)
        assert pager._viewport_rows(1, 40)[0] == ("L" + "x" * 250)[8:]
    finally:
        await cm.__aexit__(None, None, None)


async def test_empty_file(tmp_path):
    f = tmp_path / "empty.txt"
    f.write_text("")
    app, pilot, cm, pager = await open_pager(f)
    try:
        assert pager.line_count == 0
        assert pager._viewport_rows(3, 40) == ["", "", ""]
    finally:
        await cm.__aexit__(None, None, None)
