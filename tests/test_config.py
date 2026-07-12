"""Tests for the user config file (~/.config/candat/config.toml)."""

import pytest

from candat import config

pytestmark = pytest.mark.asyncio


async def test_defaults_when_no_file():
    assert not config.config_path().exists()  # isolated_config points at tmp
    assert config.load() == config.DEFAULTS


async def test_load_merges_and_type_checks():
    path = config.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# my settings\n"
        'tree_icons = "ascii"\n'
        "pager_wrap = true\n"
        'tabstop = "not a number"\n'  # wrong type -> default
        "unknown_key = 42\n"  # ignored
    )
    settings = config.load()
    assert settings["tree_icons"] == "ascii"
    assert settings["pager_wrap"] is True
    assert settings["tabstop"] == 8


async def test_broken_file_falls_back():
    path = config.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("this is [not toml")
    assert config.load() == config.DEFAULTS


async def test_save_setting_preserves_other_lines():
    path = config.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# a comment\npager_wrap = true\n")
    assert config.save_setting("tree_icons", "nerd") == path
    text = path.read_text()
    assert "# a comment" in text and "pager_wrap = true" in text
    assert 'tree_icons = "nerd"' in text
    # updating an existing key replaces its line
    config.save_setting("tree_icons", "ascii")
    text = path.read_text()
    assert 'tree_icons = "ascii"' in text and "nerd" not in text
    assert config.load()["tree_icons"] == "ascii"


async def test_pager_reads_wrap_and_tabstop(tmp_path):
    path = config.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("pager_wrap = true\ntabstop = 4\n")

    from candat.pager import TextPager

    pager = TextPager()
    assert pager.wrap is True
    assert pager._tabstop == 4
    # an explicit wrap argument still wins over the config
    assert TextPager(wrap=False).wrap is False
