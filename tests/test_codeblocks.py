"""Raw-markdown readability (fenced code highlighting, inline spans), sending
a code block to the terminal, and the remembered markdown view mode."""

from pathlib import Path

import pytest

from candat import codeblocks, session
from candat.app import CandatApp
from candat.terminal import TerminalPane
from helpers import chord, open_app, wait_for

DOC = """\
# Title

Text with `code` and **bold**.

```python
def f():
    return 1
```

```{r, echo=FALSE}
x <- 1
```
"""


# -- pure helpers -----------------------------------------------------------


@pytest.mark.parametrize(
    ("info", "language"),
    [
        ("python", "python"),
        ("Py", "python"),
        ("{r, echo=FALSE}", "r"),
        ("{r}", "r"),
        ("sh title='x'", "bash"),
        ("", None),
    ],
)
def test_fence_language(info, language):
    assert codeblocks.fence_language(info) == language


def test_fenced_blocks_closing_rules():
    lines = ["````md", "```", "inner", "```", "````", "~~~", "open to the end"]
    blocks = codeblocks.fenced_blocks(lines)
    # A shorter fence doesn't close a longer one; an unclosed block runs on.
    assert [(b.open, b.close, b.info) for b in blocks] == [(0, 4, "md"), (5, None, "")]
    assert codeblocks.fence_at(lines, 2).open == 0
    assert codeblocks.fence_at(lines, 6).open == 5


def test_block_highlights_use_the_fence_language():
    lines = DOC.splitlines()
    python_block = codeblocks.fenced_blocks(lines)[0]
    found = codeblocks.block_highlights(python_block, lines)
    assert (5, 0, 3, "keyword.function") in found  # `def` on document row 5
    assert (4, 0, None, "fence.delimiter") in found
    assert any(row == 4 and name == "fence.info" for row, _, _, name in found)


def test_unknown_language_only_styles_fences():
    lines = ["```nosuchlang", "whatever", "```"]
    found = codeblocks.block_highlights(codeblocks.fenced_blocks(lines)[0], lines)
    assert {row for row, *_ in found} == {0, 2}


def test_inline_highlights():
    line = "a `x*y*` b **bold** c [lbl](http://u) é *it*"
    names = {name for _, _, name in codeblocks.inline_highlights(line)}
    assert names == {"inline_code", "bold", "link.label", "link.uri", "italic"}
    # Emphasis inside a code span is left alone; offsets are bytes.
    spans = codeblocks.inline_highlights(line)
    assert sum(1 for *_, n in spans if n == "italic") == 1
    start, end, _ = next(s for s in spans if s[2] == "italic")
    assert line.encode()[start:end] == b"*it*"


# -- editor -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_editor_highlights_code_and_tints_block(tmp_path: Path):
    note = tmp_path / "n.md"
    note.write_text(DOC)
    async with open_app([note]) as (app, pilot):
        editor = app.active_editor
        assert editor._code_rows == frozenset({4, 5, 6, 7, 9, 10, 11})
        assert (0, 3, "keyword.function") in editor._highlights[5]
        assert any(name == "inline_code" for *_, name in editor._highlights[2])
        # Editing re-scans: closing fence removed -> the block runs to the end.
        editor.text = "```python\nx = 1\n\nprose\n"
        await pilot.pause()
        assert editor._code_rows == frozenset({0, 1, 2, 3, 4})


@pytest.mark.asyncio
async def test_code_rows_get_tinted_background(tmp_path: Path):
    from candat.theme import CODE_BLOCK_BG

    note = tmp_path / "n.md"
    note.write_text("prose\n```\ncode\n```\n")
    async with open_app([note]) as (app, pilot):
        editor = app.active_editor
        editor.move_cursor((0, 0))
        await pilot.pause()

        def bgs(y):
            return {
                s.style.bgcolor.name
                for s in editor._render_line(y)
                if s.style and s.style.bgcolor
            }

        assert CODE_BLOCK_BG in bgs(2)
        assert CODE_BLOCK_BG not in bgs(0)


# -- send code block --------------------------------------------------------


@pytest.mark.asyncio
async def test_send_code_block_to_terminal(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SHELL", "/bin/bash")
    note = tmp_path / "n.md"
    note.write_text("intro\n```bash\necho al''pha\necho be''ta\n```\nafter\n")
    async with open_app([note]) as (app, pilot):
        editor = app.active_editor
        editor.move_cursor((2, 0))
        await chord(pilot, "ctrl+c", "ctrl+b")
        terminal = app.query_one(TerminalPane)
        assert terminal.has_class("-open")
        assert app.focused is editor
        assert editor.point == (5, 0)  # past the closing fence
        text_of = lambda: "\n".join(terminal._screen.display)
        assert await wait_for(pilot, lambda: "alpha" in text_of() and "beta" in text_of())
        assert "```" not in text_of()


@pytest.mark.asyncio
async def test_send_code_block_outside_block_warns(tmp_path: Path):
    note = tmp_path / "n.md"
    note.write_text("intro\n```\nx\n```\n")
    async with open_app([note]) as (app, pilot):
        app.active_editor.move_cursor((0, 0))
        await chord(pilot, "ctrl+c", "ctrl+b")
        assert not app.query_one(TerminalPane).running


# -- remembered view mode ---------------------------------------------------


@pytest.mark.asyncio
async def test_markdown_view_mode_is_remembered(tmp_path: Path):
    first = tmp_path / "a.md"
    second = tmp_path / "b.md"
    first.write_text("a\n")
    second.write_text("b\n")
    async with open_app([first]) as (app, pilot):
        await chord(pilot, "ctrl+c", "ctrl+v")
        await chord(pilot, "ctrl+c", "ctrl+v")
        assert app.tabs.active_pane.preview_mode == "off"
    assert session.view_mode(first) == "off"

    # The file reopens raw; an unseen file follows the last choice.
    app = CandatApp([first])
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.tabs.active_pane.preview_mode == "off"
    app = CandatApp([second])
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.tabs.active_pane.preview_mode == "off"
        await chord(pilot, "ctrl+c", "ctrl+v")  # off -> split, for b only...
    assert session.view_mode(second) == "split"
    assert session.view_mode(first) == "off"  # ...a keeps its own


@pytest.mark.asyncio
async def test_view_mode_default_is_split(tmp_path: Path):
    assert session.view_mode(tmp_path / "new.md") == "split"
