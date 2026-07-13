"""Markdown mode: smart Enter, M-q reformat, tables, lists, small helpers.

The pure transforms in candat.markdown are tested directly; the editor
integration (key handling, cursor placement, renumbering on edit) through a
running app on a .md buffer.
"""

from __future__ import annotations

from pathlib import Path

from textual import events
from textual.widgets.text_area import Selection

from candat import markdown
from helpers import chord, editor_with_text

# asyncio_mode is "auto": async tests need no marker, and the pure-function
# tests below stay plain sync tests.


# -- pure: smart Enter primitives -------------------------------------------


def test_continuation_bullets():
    assert markdown.continuation("- foo") == "- "
    assert markdown.continuation("* foo") == "* "
    assert markdown.continuation("+ foo") == "+ "
    assert markdown.continuation("   - foo") == "   - "


def test_continuation_ordered_increments():
    assert markdown.continuation("1. foo") == "2. "
    assert markdown.continuation("9. foo") == "10. "
    assert markdown.continuation("3) foo") == "4) "


def test_continuation_checkbox_resets_to_unchecked():
    assert markdown.continuation("- [x] done") == "- [ ] "
    assert markdown.continuation("- [ ] todo") == "- [ ] "


def test_continuation_quotes_and_nesting():
    assert markdown.continuation("> quoted") == "> "
    assert markdown.continuation("> > deep") == "> > "
    assert markdown.continuation("> - item") == "> - "


def test_continuation_plain_text_is_none():
    assert markdown.continuation("just text") is None
    assert markdown.continuation("# heading") is None
    assert markdown.continuation("") is None


def test_is_empty_item():
    assert markdown.is_empty_item("- ")
    assert markdown.is_empty_item("2. ")
    assert markdown.is_empty_item("- [ ] ")
    assert markdown.is_empty_item("- [ ]")
    assert markdown.is_empty_item("> ")
    assert not markdown.is_empty_item("- x")
    assert not markdown.is_empty_item("plain")
    assert not markdown.is_empty_item("")


def test_in_fence():
    lines = ["text", "```py", "code", "```", "after"]
    assert not markdown.in_fence(lines, 0)
    assert not markdown.in_fence(lines, 1)  # the opener itself
    assert markdown.in_fence(lines, 2)
    assert markdown.in_fence(lines, 3)  # the closing line
    assert not markdown.in_fence(lines, 4)


def test_unclosed_fence_opener():
    assert markdown.unclosed_fence_opener(["```py", "code"], 0) == "```"
    assert markdown.unclosed_fence_opener(["  ~~~~", "x"], 0) == "  ~~~~"
    # balanced document: nothing to close
    assert markdown.unclosed_fence_opener(["```", "x", "```"], 0) is None
    assert markdown.unclosed_fence_opener(["text"], 0) is None


# -- pure: tables -------------------------------------------------------------


def test_align_table_pads_columns():
    lines = ["|a|bb|", "|-|-|", "|long|x|"]
    start, end, out = markdown.align_table(lines, 0)
    assert (start, end) == (0, 2)
    assert out == [
        "| a    | bb  |",
        "|------|-----|",
        "| long | x   |",
    ]


def test_align_table_honours_alignment_colons():
    lines = ["| head | mid | tail |", "| :--- | :-: | ---: |", "| a | b | c |"]
    _, _, out = markdown.align_table(lines, 2)
    # centre pads both sides, right pads left
    assert out == [
        "| head | mid | tail |",
        "|:-----|:---:|-----:|",
        "| a    |  b  |    c |",
    ]


def test_align_table_ragged_rows_gain_cells():
    lines = ["| a | b |", "| c |"]
    _, _, out = markdown.align_table(lines, 0)
    assert out == ["| a   | b   |", "| c   |     |"]


def test_align_table_preserves_indent_and_wide_chars():
    lines = ["  | 名前 | x |", "  | a | b |"]
    _, _, out = markdown.align_table(lines, 0)
    assert out[0] == "  | 名前 | x   |"
    assert out[1] == "  | a    | b   |"


def test_align_table_not_a_table():
    assert markdown.align_table(["plain text"], 0) is None


def test_cell_index_and_bounds():
    line = "| aa | b  |    |"
    bounds = markdown.cell_bounds(line)
    assert bounds == [(2, 4), (7, 8), (12, 12)]
    assert markdown.cell_index(line, 0) == 0
    assert markdown.cell_index(line, 3) == 0
    assert markdown.cell_index(line, 7) == 1
    assert markdown.cell_index(line, 14) == 2
    assert markdown.cell_index(line, 99) == 2


def test_blank_row_like():
    assert markdown.blank_row_like("| aa | b |") == "|    |   |"


def test_is_separator_row():
    assert markdown.is_separator_row("|---|:-:|")
    assert markdown.is_separator_row("| :--- | ---: |")
    assert not markdown.is_separator_row("| a | b |")


# -- pure: fill (M-q) ---------------------------------------------------------


def test_fill_paragraph_wraps_long_line():
    words = "word " * 30
    start, end, out = markdown.fill_paragraph([words.strip()], 0, 40)
    assert (start, end) == (0, 0)
    assert all(len(line) <= 40 for line in out)
    assert " ".join(out) == words.strip()


def test_fill_paragraph_joins_short_lines():
    lines = ["one two", "three four", "", "next para"]
    start, end, out = markdown.fill_paragraph(lines, 0, 80)
    assert (start, end) == (0, 1)
    assert out == ["one two three four"]


def test_fill_list_item_hanging_indent():
    text = "- " + "word " * 20
    _, _, out = markdown.fill_paragraph([text.strip()], 0, 30)
    assert out[0].startswith("- word")
    assert all(line.startswith("  word") for line in out[1:])


def test_fill_list_item_from_continuation_line():
    lines = ["- first words", "continuation of the item"]
    start, end, out = markdown.fill_paragraph(lines, 1, 80)
    assert (start, end) == (0, 1)
    assert out == ["- first words continuation of the item"]


def test_fill_checkbox_item_keeps_box():
    text = "- [ ] " + "word " * 20
    _, _, out = markdown.fill_paragraph([text.strip()], 0, 30)
    assert out[0].startswith("- [ ] word")
    assert all(line.startswith("      word") for line in out[1:])


def test_fill_quote_block():
    lines = ["> " + "word " * 20]
    _, _, out = markdown.fill_paragraph([lines[0].strip()], 0, 30)
    assert all(line.startswith("> ") for line in out)
    assert all(len(line) <= 30 for line in out)


def test_fill_refuses_headings_tables_fences_blank():
    assert markdown.fill_paragraph(["# heading"], 0, 80) is None
    assert markdown.fill_paragraph(["| a | b |"], 0, 80) is None
    assert markdown.fill_paragraph([""], 0, 80) is None
    assert markdown.fill_paragraph(["```", "code line", "```"], 1, 80) is None
    assert markdown.fill_paragraph(["    indented code"], 0, 80) is None


def test_fill_stops_at_block_boundaries():
    lines = ["# head", "one two", "three", "- item"]
    start, end, out = markdown.fill_paragraph(lines, 1, 80)
    assert (start, end) == (1, 2)
    assert out == ["one two three"]


def test_fill_does_not_eat_setext_underline():
    lines = ["Title words here", "====="]
    start, end, _ = markdown.fill_paragraph(lines, 0, 80)
    assert (start, end) == (0, 0)


def test_reformat_dispatches_table_vs_paragraph():
    lines = ["|a|b|", "", "some words"]
    assert markdown.reformat(lines, 0, 80)[3] == "table"
    assert markdown.reformat(lines, 2, 80)[3] == "paragraph"
    assert markdown.reformat(["```", "|a|", "```"], 1, 80) is None


# -- pure: ordered lists -------------------------------------------------------


def test_renumber_sequences_from_first_number():
    lines = ["3. a", "7. b", "1. c"]
    start, end, out = markdown.renumber(lines, 1)
    assert (start, end) == (0, 2)
    assert out == ["3. a", "4. b", "5. c"]


def test_renumber_handles_nesting_and_keeps_delimiters():
    lines = ["1. a", "   1. x", "   5. y", "1) b"]
    _, _, out = markdown.renumber(lines, 0)
    assert out == ["1. a", "   1. x", "   2. y", "2) b"]


def test_renumber_bullet_breaks_run():
    lines = ["1. a", "5. b", "- bullet", "7. c", "1. d"]
    _, _, out = markdown.renumber(lines, 0)
    assert out == ["1. a", "2. b", "- bullet", "7. c", "8. d"]


def test_renumber_ignores_preceding_paragraph():
    lines = ["intro text", "1. a", "1. b"]
    start, end, out = markdown.renumber(lines, 2)
    assert start == 1
    assert out == ["1. a", "2. b"]


def test_renumber_none_when_already_sequential():
    assert markdown.renumber(["1. a", "2. b"], 0) is None
    assert markdown.renumber(["- a", "- b"], 0) is None


def test_indent_item_nests_under_previous_sibling():
    assert markdown.indent_item(["- a", "- b"], 1) == "  - b"
    assert markdown.indent_item(["1. a", "2. b"], 1) == "   2. b"
    # first item has nothing to nest under
    assert markdown.indent_item(["- a"], 0) is None
    # already nested: idempotent
    assert markdown.indent_item(["- a", "  - b"], 1) is None


def test_outdent_item():
    assert markdown.outdent_item(["- a", "  - b"], 1) == "- b"
    assert markdown.outdent_item(["  - only"], 0) == "- only"
    assert markdown.outdent_item(["- top"], 0) is None


# -- pure: small helpers --------------------------------------------------------


def test_toggle_checkbox():
    assert markdown.toggle_checkbox("- foo") == "- [ ] foo"
    assert markdown.toggle_checkbox("- [ ] foo") == "- [x] foo"
    assert markdown.toggle_checkbox("- [X] foo") == "- [ ] foo"
    assert markdown.toggle_checkbox("  2. [ ] foo") == "  2. [x] foo"
    assert markdown.toggle_checkbox("- [ ]") == "- [x]"
    assert markdown.toggle_checkbox("plain text") is None


def test_toggle_emphasis_bold_italic_compose():
    assert markdown.toggle_emphasis("x", "bold") == "**x**"
    assert markdown.toggle_emphasis("**x**", "bold") == "x"
    assert markdown.toggle_emphasis("x", "italic") == "*x*"
    assert markdown.toggle_emphasis("*x*", "italic") == "x"
    assert markdown.toggle_emphasis("**x**", "italic") == "***x***"
    assert markdown.toggle_emphasis("***x***", "italic") == "**x**"
    assert markdown.toggle_emphasis("***x***", "bold") == "*x*"


def test_toggle_emphasis_code():
    assert markdown.toggle_emphasis("x", "code") == "`x`"
    assert markdown.toggle_emphasis("`x`", "code") == "x"


def test_word_at_and_is_url():
    assert markdown.word_at("foo bar", 5) == (4, 7)
    assert markdown.word_at("foo bar", 0) == (0, 3)
    assert markdown.word_at("   ", 1) is None
    assert markdown.is_url("https://example.org/a?b=1")
    assert markdown.is_url("  http://x.y  ")
    assert not markdown.is_url("not a url")
    assert not markdown.is_url("https://has space")


# -- integration ----------------------------------------------------------------


def md_file(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "doc.md"
    path.write_text(text)
    return path


async def test_enter_continues_bullet(tmp_path):
    async with editor_with_text(path=md_file(tmp_path, "- one\n")) as (app, pilot, editor):
        editor.selection = Selection((0, 5), (0, 5))
        await pilot.press("enter")
        assert editor.text.splitlines()[1] == "- "
        assert editor.point == (1, 2)
        await pilot.press("t", "w", "o")
        assert editor.text.splitlines()[1] == "- two"


async def test_enter_on_empty_item_ends_list(tmp_path):
    async with editor_with_text(path=md_file(tmp_path, "- one\n- \n")) as (app, pilot, editor):
        editor.selection = Selection((1, 2), (1, 2))
        await pilot.press("enter")
        assert editor.text.splitlines()[1] == ""
        assert editor.point == (1, 0)


async def test_enter_mid_item_splits_without_stray_space(tmp_path):
    async with editor_with_text(path=md_file(tmp_path, "- second bullet\n")) as (
        app,
        pilot,
        editor,
    ):
        editor.selection = Selection((0, 8), (0, 8))  # after "second"
        await pilot.press("enter")
        assert editor.text.splitlines() == ["- second", "- bullet"]


async def test_abandoning_ordered_insert_restores_tail_number(tmp_path):
    async with editor_with_text(path=md_file(tmp_path, "1. a\n2. b\n")) as (
        app,
        pilot,
        editor,
    ):
        editor.selection = Selection((0, 4), (0, 4))
        await pilot.press("enter")  # inserts "2. ", b becomes 3.
        assert editor.text.splitlines() == ["1. a", "2. ", "3. b"]
        await pilot.press("enter")  # abandon: the tail returns to 2.
        assert editor.text.splitlines() == ["1. a", "", "2. b"]


async def test_enter_renumbers_ordered_list(tmp_path):
    async with editor_with_text(path=md_file(tmp_path, "1. a\n2. b\n")) as (app, pilot, editor):
        editor.selection = Selection((0, 4), (0, 4))
        await pilot.press("enter")
        await pilot.press("x")
        assert editor.text.splitlines() == ["1. a", "2. x", "3. b"]


async def test_enter_in_fence_is_plain(tmp_path):
    text = "```\n- not a list\n```\n"
    async with editor_with_text(path=md_file(tmp_path, text)) as (app, pilot, editor):
        editor.selection = Selection((1, 12), (1, 12))
        await pilot.press("enter")
        assert editor.text.splitlines()[2] == ""


async def test_enter_closes_open_fence(tmp_path):
    async with editor_with_text(path=md_file(tmp_path, "```py\n")) as (app, pilot, editor):
        editor.selection = Selection((0, 5), (0, 5))
        await pilot.press("enter")
        assert editor.text.splitlines() == ["```py", "", "```"]
        assert editor.point == (1, 0)


async def test_fill_paragraph_via_meta_q(tmp_path):
    long = "word " * 30
    async with editor_with_text(path=md_file(tmp_path, long.strip() + "\n")) as (app, pilot, editor):
        await pilot.press("escape", "q")  # M-q via ESC prefix
        lines = editor.text.splitlines()
        assert len(lines) > 1
        assert all(len(line) <= 80 for line in lines)


async def test_meta_q_aligns_table(tmp_path):
    async with editor_with_text(path=md_file(tmp_path, "|a|bb|\n|-|-|\n|long|x|\n")) as (
        app,
        pilot,
        editor,
    ):
        await pilot.press("alt+q")
        assert editor.text.splitlines()[0] == "| a    | bb  |"


async def test_tab_moves_between_table_cells(tmp_path):
    async with editor_with_text(path=md_file(tmp_path, "| a | b |\n| c | d |\n")) as (
        app,
        pilot,
        editor,
    ):
        editor.selection = Selection((0, 2), (0, 2))
        await pilot.press("tab")
        # the table is aligned (min column width 3) and point lands on "b"
        assert editor.text.splitlines()[0] == "| a   | b   |"
        assert editor.point == (0, 8)
        await pilot.press("tab")
        assert editor.point == (1, 2)  # next row, cell "c"
        await pilot.press("shift+tab")
        assert editor.point == (0, 8)


async def test_tab_off_last_cell_appends_row(tmp_path):
    async with editor_with_text(path=md_file(tmp_path, "| a | b |\n")) as (app, pilot, editor):
        editor.selection = Selection((0, 6), (0, 6))
        await pilot.press("tab")
        lines = editor.text.splitlines()
        assert len(lines) == 2
        assert lines[1] == "|     |     |"
        assert editor.point == (1, 2)


async def test_tab_indents_list_item_and_renumbers(tmp_path):
    async with editor_with_text(path=md_file(tmp_path, "1. a\n2. b\n3. c\n")) as (
        app,
        pilot,
        editor,
    ):
        editor.selection = Selection((1, 4), (1, 4))
        await pilot.press("tab")
        # b nests under a; the outer list renumbers around it
        assert editor.text.splitlines() == ["1. a", "   2. b", "2. c"]
        await pilot.press("shift+tab")
        assert editor.text.splitlines() == ["1. a", "2. b", "3. c"]


async def test_checkbox_toggle_chord(tmp_path):
    async with editor_with_text(path=md_file(tmp_path, "- [ ] task\n")) as (app, pilot, editor):
        await chord(pilot, "ctrl+c", "ctrl+t")
        assert editor.text.splitlines()[0] == "- [x] task"
        await chord(pilot, "ctrl+c", "ctrl+t")
        assert editor.text.splitlines()[0] == "- [ ] task"


async def test_bold_word_at_point_chord(tmp_path):
    async with editor_with_text(path=md_file(tmp_path, "make this bold\n")) as (
        app,
        pilot,
        editor,
    ):
        editor.selection = Selection((0, 11), (0, 11))  # inside "bold"
        await chord(pilot, "ctrl+c", "b")
        assert editor.text.splitlines()[0] == "make this **bold**"


async def test_emphasis_on_region(tmp_path):
    async with editor_with_text(path=md_file(tmp_path, "some words here\n")) as (
        app,
        pilot,
        editor,
    ):
        editor.selection = Selection((0, 5), (0, 10))  # "words"
        await chord(pilot, "ctrl+c", "i")
        assert editor.text.splitlines()[0] == "some *words* here"


async def test_paste_url_over_selection_makes_link(tmp_path):
    async with editor_with_text(path=md_file(tmp_path, "see the docs here\n")) as (
        app,
        pilot,
        editor,
    ):
        editor.selection = Selection((0, 8), (0, 12))  # "docs"
        await editor._on_paste(events.Paste("https://example.org/docs"))
        await pilot.pause()
        assert editor.text.splitlines()[0] == "see the [docs](https://example.org/docs) here"


async def test_paste_plain_text_unaffected(tmp_path):
    async with editor_with_text(path=md_file(tmp_path, "ab\n")) as (app, pilot, editor):
        editor.selection = Selection((0, 0), (0, 2))
        await editor._on_paste(events.Paste("plain"))
        await pilot.pause()
        assert editor.text.splitlines()[0] == "plain"


async def test_smart_keys_off_outside_markdown():
    async with editor_with_text("- one\n") as (app, pilot, editor):
        assert editor.language is None
        editor.selection = Selection((0, 5), (0, 5))
        await pilot.press("enter")
        assert editor.text.splitlines()[1:] == [""]
