"""Tests for config/Makefile syntax highlighting and the soft-wrap toggle."""

from pathlib import Path

import pytest

from candat.editor import language_for
from helpers import chord, editor_with_text, open_app

pytestmark = pytest.mark.asyncio


# -- language detection (pure) ----------------------------------------------


async def test_language_for_by_filename_and_extension():
    cases = {
        "Makefile": "make",
        "GNUmakefile": "make",
        "build.mk": "make",
        "Dockerfile": "dockerfile",
        "Dockerfile.prod": "dockerfile",
        "Containerfile": "dockerfile",
        "app.dockerfile": "dockerfile",
        "setup.cfg": "ini",
        "tox.ini": "ini",
        ".editorconfig": "ini",
        "settings.conf": "ini",
        ".bashrc": "bash",
        ".zshrc": "bash",
        ".env": "bash",
        ".env.local": "bash",
        "main.py": "python",
        "notes.txt": None,
    }
    for name, expected in cases.items():
        assert language_for(Path("/x") / name) == expected, name


# -- highlighting actually registers ----------------------------------------


@pytest.mark.parametrize(
    "name,content,lang",
    [
        ("Makefile", "CC = gcc\nall:\n\tgcc -o app main.c\n", "make"),
        ("setup.cfg", "[metadata]\nname = candat\n", "ini"),
        ("Dockerfile", "FROM python:3.13\nRUN pip install uv\n", "dockerfile"),
    ],
)
async def test_config_files_highlight(tmp_path, name, content, lang):
    path = tmp_path / name
    path.write_text(content)
    async with open_app([path]) as (app, pilot):
        editor = app.active_editor
        assert editor.language == lang
        assert lang in editor.available_languages


# -- soft wrap ---------------------------------------------------------------


async def test_toggle_soft_wrap_default_off():
    async with editor_with_text("x = 1\n") as (app, pilot, editor):
        assert editor.soft_wrap is False
        await chord(pilot, "ctrl+x", "w")
        assert editor.soft_wrap is True
        # status bar reflects it
        assert "wrap" in str(app.query_one("StatusBar").content)
        await chord(pilot, "ctrl+x", "w")
        assert editor.soft_wrap is False


async def test_soft_wrap_is_per_buffer(tmp_path):
    a = tmp_path / "a.py"
    b = tmp_path / "b.py"
    a.write_text("a\n")
    b.write_text("b\n")
    async with open_app([a, b]) as (app, pilot):
        # b is active (opened last); wrap it.
        await chord(pilot, "ctrl+x", "w")
        assert app.active_editor.soft_wrap is True
        # switch to the other buffer: still off there.
        await chord(pilot, "ctrl+x", "b")
        await pilot.press("enter")
        await pilot.pause()
        assert app.active_editor.soft_wrap is False
