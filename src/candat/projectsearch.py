"""Project-wide search (C-x g): ripgrep if available, grep -rEn otherwise,
with a pickable results list."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from rich.text import Text
from textual import events, on
from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.widgets import OptionList
from textual.widgets.option_list import Option

MAX_RESULTS = 500

EXCLUDED_DIRS = (".git", ".venv", "venv", "__pycache__", ".pytest_cache", "node_modules")


def search_project(root: Path, pattern: str) -> list[tuple[Path, int, str]]:
    """Regex-search all files under root; returns (path, line number, line)."""
    if shutil.which("rg"):
        cmd = [
            "rg", "--line-number", "--no-heading", "--color=never",
            "--smart-case", "--max-columns=300", "--regexp", pattern, str(root),
        ]
    else:
        cmd = ["grep", "-rEn", "--binary-files=without-match"]
        cmd += [f"--exclude-dir={d}" for d in EXCLUDED_DIRS]
        cmd += ["-e", pattern, str(root)]
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, errors="replace", timeout=30
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    results: list[tuple[Path, int, str]] = []
    for raw in out.stdout.splitlines():
        path_str, sep, rest = raw.partition(":")
        line_str, sep2, snippet = rest.partition(":")
        if not (sep and sep2 and line_str.isdigit()):
            continue
        results.append((Path(path_str), int(line_str), snippet.strip()))
        if len(results) >= MAX_RESULTS:
            break
    return results


class SearchResultsScreen(ModalScreen["tuple[Path, int] | None"]):
    """Pick a match; returns (path, line) or None."""

    CSS = """
    SearchResultsScreen {
        align: center middle;
    }
    SearchResultsScreen OptionList {
        width: 90%;
        max-height: 80%;
        background: $background;
        border: solid $primary;
        padding: 0 1;
    }
    """

    def __init__(
        self, root: Path, pattern: str, results: list[tuple[Path, int, str]]
    ) -> None:
        super().__init__()
        self._root = root
        self._pattern = pattern
        self._results = results

    def compose(self) -> ComposeResult:
        options = []
        for path, line, snippet in self._results:
            try:
                shown = path.relative_to(self._root)
            except ValueError:
                shown = path
            label = Text.assemble(
                (f"{shown}:{line}", "bold"), ("  ", ""), (snippet[:200], "dim")
            )
            options.append(Option(label, id=f"{path}\n{line}"))
        option_list = OptionList(*options)
        suffix = " (capped)" if len(self._results) >= MAX_RESULTS else ""
        option_list.border_title = (
            f"{len(self._results)} matches for {self._pattern!r}{suffix}"
        )
        yield option_list

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()

    @on(OptionList.OptionSelected)
    def _selected(self, event: OptionList.OptionSelected) -> None:
        path_str, _, line_str = (event.option.id or "").rpartition("\n")
        self.dismiss((Path(path_str), int(line_str)))

    def on_key(self, event: events.Key) -> None:
        if event.key in ("escape", "ctrl+g", "q"):
            event.stop()
            self.dismiss(None)
        elif event.key in ("ctrl+n", "ctrl+p"):
            event.stop()
            option_list = self.query_one(OptionList)
            if event.key == "ctrl+n":
                option_list.action_cursor_down()
            else:
                option_list.action_cursor_up()
