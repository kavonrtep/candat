"""Tests for the windowed large-file pager (stage 1)."""

import pytest
from textual.app import App, ComposeResult

from candat.pager import TextPager
from helpers import chord, open_app

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


async def test_search_forward_backward_and_missing(tmp_path):
    app, pilot, cm, pager = await open_pager(make_file(tmp_path))
    try:
        # 5-digit targets are unique (appending a digit exceeds 99999).
        assert pager.search("line 88888") is True
        assert pager.top_line == 88888 and pager.match_line == 88888
        assert pager.search("line 11111", forward=False) is True
        assert pager.top_line == 11111
        assert pager.search("no-such-text-zzz") is False
        assert pager.match_line is None
    finally:
        await cm.__aexit__(None, None, None)


async def test_search_smart_case(tmp_path):
    f = tmp_path / "m.txt"
    lines = [f"line {i}" for i in range(1000)]
    lines[42] = "UNIQUEMARKER here"
    f.write_text("\n".join(lines) + "\n")
    app, pilot, cm, pager = await open_pager(f)
    try:
        # lowercase query is case-insensitive, so it matches uppercase content
        assert pager.search("uniquemarker") is True
        assert pager.top_line == 42
    finally:
        await cm.__aexit__(None, None, None)


async def test_search_repeats_within_and_across_lines(tmp_path):
    f = tmp_path / "m.txt"
    f.write_text("".join(f"row {i} tok A tok B\n" for i in range(500)))
    app, pilot, cm, pager = await open_pager(f)
    try:
        assert pager.search("tok") is True  # first 'tok' on line 0
        assert pager.top_line == 0
        first = pager._match_byte
        assert pager.search_next(True) is True  # second 'tok', still line 0
        assert pager.top_line == 0 and pager._match_byte > first
        assert pager.search_next(True) is True  # first 'tok' on line 1
        assert pager.top_line == 1
        assert pager.search_next(False) is True  # step back to line 0
        assert pager.top_line == 0
    finally:
        await cm.__aexit__(None, None, None)


async def test_highlight_all_visible_matches(tmp_path):
    f = tmp_path / "h.txt"
    f.write_text("".join(f"row {i} tok tok\n" for i in range(50)))
    app, pilot, cm, pager = await open_pager(f)
    try:
        pager.search("tok")
        styled = [s for s in pager.render().spans if s.style == pager.HIGHLIGHT]
        assert len(styled) > 1  # every visible 'tok' is styled, not just one
    finally:
        await cm.__aexit__(None, None, None)


async def test_goto_percent(tmp_path):
    app, pilot, cm, pager = await open_pager(make_file(tmp_path))
    try:
        pager.goto_percent(50)
        assert abs(pager.top_line - 50_000) <= 1
        pager.goto_percent(100)
        assert pager.top_line == 99_999
        pager.goto_percent(0)
        assert pager.top_line == 0
    finally:
        await cm.__aexit__(None, None, None)


async def test_large_file_routes_to_pager(tmp_path):
    big = tmp_path / "huge.log"
    big.write_text(("x" * 80 + "\n") * 150_000)  # ~12 MB
    async with open_app([big]) as (app, pilot):
        pane = app.active_pane
        pager = pane.pager
        for _ in range(80):
            await pilot.pause()
            if not pager._indexing:
                break
        assert pane.is_pager
        assert pane.editor.text == ""  # the file was NOT loaded into the editor
        assert pager.line_count == 150_000
        assert app.active_pane.visible_widget is pager
        assert app.focused is pager  # so pager keys (C-s, g/G, …) reach it
        # C-x w toggles the pager's wrap
        await chord(pilot, "ctrl+x", "w")
        assert pager.wrap
        # navigation works and reports position
        pager.action_scroll_lines(5)
        assert pager.top_line == 5

        # C-s opens the pager's search prompt (not the editor's isearch)
        from candat.dialogs import PromptScreen
        from textual.widgets import Input

        await pilot.press("ctrl+s")
        await pilot.pause()
        assert isinstance(app.screen, PromptScreen)
        await pilot.press("escape")
        await pilot.pause()
        # M-g jumps to a line
        await pilot.press("alt+g")
        await pilot.pause()
        app.screen.query_one(Input).value = "1000"
        await pilot.press("enter")
        await pilot.pause()
        assert pager.top_line == 999


async def test_empty_file(tmp_path):
    f = tmp_path / "empty.txt"
    f.write_text("")
    app, pilot, cm, pager = await open_pager(f)
    try:
        assert pager.line_count == 0
        assert pager._viewport_rows(3, 40) == ["", "", ""]
    finally:
        await cm.__aexit__(None, None, None)
