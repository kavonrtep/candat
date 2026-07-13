"""System-clipboard mirroring (the `system_clipboard` config key).

Textual records the last OSC 52 copy on `app.clipboard`, so the mirror is
observable headless. The wl-copy/xclip tool fallback is disabled suite-wide
by the `no_clipboard_tools` fixture; its command selection is tested
directly with a stubbed subprocess.
"""

from __future__ import annotations

from textual.widgets.text_area import Selection

from candat import clipboard, config
from candat.nav import FileTree
from helpers import editor_with_text, open_app

# asyncio_mode is "auto"; pure tests stay sync.


def set_mode(value: str) -> None:
    config.save_setting("system_clipboard", value)  # isolated XDG config


def test_mode_defaults_and_validates():
    assert clipboard.mode() == "copy"
    set_mode("all")
    assert clipboard.mode() == "all"
    set_mode("everything")  # unknown value falls back to the default
    assert clipboard.mode() == "copy"


def test_tool_selection_prefers_the_session_type(monkeypatch):
    monkeypatch.setattr(clipboard.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.delenv("DISPLAY", raising=False)
    assert clipboard._tool_command() == ["wl-copy"]
    monkeypatch.delenv("WAYLAND_DISPLAY")
    monkeypatch.setenv("DISPLAY", ":0")
    assert clipboard._tool_command() == ["xclip", "-selection", "clipboard"]
    # no display of either kind, not macOS -> no tool
    monkeypatch.delenv("DISPLAY")
    monkeypatch.setattr(clipboard.shutil, "which", lambda name: None)
    assert clipboard._tool_command() is None


async def test_copy_region_mirrors_by_default():
    async with editor_with_text("hello world\n") as (app, pilot, editor):
        editor.selection = Selection((0, 0), (0, 5))
        await pilot.press("alt+w")  # M-w copy-region
        assert app.clipboard == "hello"


async def test_kill_does_not_mirror_by_default():
    async with editor_with_text("secret line\n") as (app, pilot, editor):
        await pilot.press("ctrl+k")
        assert app.kill_ring.current == "secret line"
        assert app.clipboard == ""


async def test_all_mode_mirrors_kills_and_accumulates():
    set_mode("all")
    async with editor_with_text("first\nsecond\n") as (app, pilot, editor):
        await pilot.press("ctrl+k")
        assert app.clipboard == "first"
        await pilot.press("ctrl+k")  # chained kill grows the same entry
        assert app.clipboard == "first\n"


async def test_off_mode_mirrors_nothing():
    set_mode("off")
    async with editor_with_text("hello\n") as (app, pilot, editor):
        editor.selection = Selection((0, 0), (0, 5))
        await pilot.press("alt+w")
        assert app.kill_ring.current == "hello"
        assert app.clipboard == ""


async def test_tree_copy_path_mirrors(tmp_path):
    (tmp_path / "a.txt").write_text("x\n")
    async with open_app([tmp_path]) as (app, pilot):
        tree = app.query_one(FileTree)
        tree.focus()
        await pilot.press("down", "w")
        assert app.clipboard == app.kill_ring.current
        assert app.clipboard.startswith("/")
