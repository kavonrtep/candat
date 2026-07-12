"""Tests for the navigation panel's file-tree filter."""

import pytest
from textual.widgets import Input

from candat.nav import FileTree, NavPanel
from helpers import chord, open_app

pytestmark = pytest.mark.asyncio


def visible_names(tree: FileTree) -> set[str]:
    names: set[str] = set()

    def walk(node):
        for child in node.children:
            if child.data is not None:
                names.add(child.data.path.name)
            walk(child)

    walk(tree.root)
    return names


def make_tree(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "alpha.py").write_text("a\n")
    (tmp_path / "src" / "beta.py").write_text("b\n")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "guide.md").write_text("g\n")
    (tmp_path / "readme.txt").write_text("r\n")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "alpha_secret.py").write_text("x\n")
    return tmp_path


async def test_filter_narrows_and_reveals_matches(tmp_path):
    root = make_tree(tmp_path)
    async with open_app([root]) as (app, pilot):
        tree = app.query_one(FileTree)
        await tree.set_filter("alpha")
        await pilot.pause()
        names = visible_names(tree)
        # The nested match and its ancestor dir are revealed...
        assert "alpha.py" in names
        assert "src" in names
        # ...unrelated files and the excluded .git match are gone.
        assert "beta.py" not in names
        assert "guide.md" not in names
        assert "alpha_secret.py" not in names


async def test_filter_matches_on_path_not_just_name(tmp_path):
    root = make_tree(tmp_path)
    async with open_app([root]) as (app, pilot):
        tree = app.query_one(FileTree)
        await tree.set_filter("docs/gu")
        await pilot.pause()
        names = visible_names(tree)
        assert "guide.md" in names
        assert "alpha.py" not in names


async def test_clearing_filter_restores_full_tree(tmp_path):
    root = make_tree(tmp_path)
    async with open_app([root]) as (app, pilot):
        tree = app.query_one(FileTree)
        await tree.set_filter("alpha")
        await pilot.pause()
        assert "beta.py" not in visible_names(tree)
        await tree.set_filter("")
        await pilot.pause()
        names = visible_names(tree)
        assert {"src", "docs", "readme.txt"} <= names


async def test_slash_focuses_filter_and_escape_clears(tmp_path):
    root = make_tree(tmp_path)
    async with open_app([root]) as (app, pilot):
        tree = app.query_one(FileTree)
        filter_input = app.query_one(Input)
        tree.focus()
        await pilot.pause()
        await pilot.press("slash")
        await pilot.pause()
        assert app.focused is filter_input
        # Type a query into the filter, let the debounce fire.
        filter_input.value = "beta"
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert filter_input.value == ""
        assert app.focused is tree


async def test_tree_icon_sets_and_cycle(tmp_path, monkeypatch):
    from candat.nav import TREE_ICON_SETS, resolve_icon_set

    # $CANDAT_TREE_ICONS selects the set; an invalid value falls back to emoji.
    monkeypatch.setenv("CANDAT_TREE_ICONS", "nerd")
    assert resolve_icon_set(None) == "nerd"
    monkeypatch.setenv("CANDAT_TREE_ICONS", "bogus")
    assert resolve_icon_set(None) == "emoji"
    monkeypatch.delenv("CANDAT_TREE_ICONS", raising=False)

    root = make_tree(tmp_path)
    async with open_app([root]) as (app, pilot):
        tree = app.query_one(FileTree)
        assert tree.ICON_NODE == TREE_ICON_SETS["emoji"][0]
        assert tree.cycle_icons() == "nerd"
        assert tree.ICON_NODE == TREE_ICON_SETS["nerd"][0]
        # via the M-x action (nerd -> ascii)
        app.action_cycle_tree_icons()
        assert tree._icon_set == "ascii"
        assert tree.ICON_FILE == TREE_ICON_SETS["ascii"][2]

    # the cycled choice was persisted, and the config now drives the default
    from candat import config

    assert 'tree_icons = "ascii"' in config.config_path().read_text()
    assert resolve_icon_set(None) == "ascii"


async def test_tree_resize_keyboard_and_persist(tmp_path):
    from candat import config
    from candat.nav import MIN_TREE_WIDTH, NavPanel

    root = make_tree(tmp_path)
    async with open_app([root]) as (app, pilot):
        nav = app.query_one(NavPanel)
        assert nav.outer_size.width == 32  # the default
        await chord(pilot, "ctrl+x", "right_curly_bracket")  # C-x }
        assert nav.outer_size.width == 36
        assert "tree_width = 36" in config.config_path().read_text()
        await chord(pilot, "ctrl+x", "left_curly_bracket")  # C-x {
        await chord(pilot, "ctrl+x", "left_curly_bracket")
        assert nav.outer_size.width == 28
        # clamped at the minimum, never collapses to nothing
        for _ in range(10):
            await chord(pilot, "ctrl+x", "left_curly_bracket")
        assert nav.outer_size.width == MIN_TREE_WIDTH

    # a fresh app starts at the persisted width
    async with open_app([root]) as (app, pilot):
        assert app.query_one(NavPanel).outer_size.width == MIN_TREE_WIDTH


async def test_tree_resize_splitter_drag_and_reset(tmp_path):
    from types import SimpleNamespace

    from candat.nav import DEFAULT_TREE_WIDTH, NavPanel, TreeSplitter

    root = make_tree(tmp_path)
    async with open_app([root]) as (app, pilot):
        nav = app.query_one(NavPanel)
        splitter = app.query_one(TreeSplitter)
        # drag: press, move to column 45, release (persists)
        splitter.on_mouse_down(SimpleNamespace(stop=lambda: None))
        splitter.on_mouse_move(SimpleNamespace(screen_x=45))
        await pilot.pause()
        assert nav.styles.width.value == 45
        splitter.on_mouse_up(SimpleNamespace(stop=lambda: None, screen_x=45))
        from candat import config

        assert config.load()["tree_width"] == 45
        # double-click resets to the default
        splitter.on_click(SimpleNamespace(stop=lambda: None, chain=2))
        await pilot.pause()
        assert nav.styles.width.value == DEFAULT_TREE_WIDTH
        # the splitter draws a │ rule, not the widget's name spilled vertically
        rendered = splitter.render().plain
        assert set(rendered) <= {"│", "\n"}
        assert rendered.count("│") == max(1, splitter.size.height)


async def test_no_match_hides_everything(tmp_path):
    root = make_tree(tmp_path)
    async with open_app([root]) as (app, pilot):
        tree = app.query_one(FileTree)
        await tree.set_filter("zzz-nothing")
        await pilot.pause()
        assert visible_names(tree) == set()
