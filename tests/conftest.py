"""Shared pytest fixtures for the candat test-suite.

- data fixtures: `make_csv`/`sample_csv`/`sample_tsv`/`text_file`.
- environment: `bash_shell` for terminal tests.

The running-app helpers live in `helpers.py` as async context managers
(`open_app`, `editor_with_text`) rather than fixtures: Textual's run_test()
must be entered and exited in the same async context, which a fixture that
tears down after the test cannot guarantee.
"""

from __future__ import annotations

from pathlib import Path

import pytest


# -- environment -------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolated_config(tmp_path_factory, monkeypatch):
    """Keep tests away from the user's real config and state (cycle-tree-icons
    persists its choice; quitting saves the session; edits are autosaved to a
    recovery dir).

    We isolate config/state via XDG env vars, but NOT XDG_CACHE_HOME — the
    tree-sitter language pack caches compiled grammars there, and a fresh empty
    cache per test makes grammar loading flaky. The recovery dir (the only
    cache candat writes) is isolated by patching its accessor directly.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path_factory.mktemp("xdg")))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path_factory.mktemp("state")))
    monkeypatch.delenv("CANDAT_TREE_ICONS", raising=False)
    from candat import recovery

    recovery_root = tmp_path_factory.mktemp("recovery")
    monkeypatch.setattr(recovery, "recovery_dir", lambda: recovery_root / "candat")


@pytest.fixture(autouse=True)
def no_clipboard_tools(monkeypatch):
    """Copy actions must never touch the developer's real clipboard while
    the suite runs: disable the wl-copy/xclip fallback. The OSC 52 channel
    is harmless headless and records on app.clipboard for assertions."""
    from candat import clipboard

    monkeypatch.setattr(clipboard, "_copy_via_tool", lambda text: None)


@pytest.fixture
def bash_shell(monkeypatch):
    """Point SHELL at bash with a plain prompt, for terminal tests."""
    monkeypatch.setenv("SHELL", "/bin/bash")
    monkeypatch.setenv("PS1", "$ ")


# -- data-file fixtures ------------------------------------------------------


@pytest.fixture
def make_csv(tmp_path):
    """Factory: write a CSV of `rows` rows (id,name,value) and return its path."""

    def _make(rows: int = 50, name: str = "data.csv") -> Path:
        from helpers import write_csv

        path = tmp_path / name
        write_csv(path, rows)
        return path

    return _make


@pytest.fixture
def sample_csv(make_csv) -> Path:
    """A 50-row CSV file."""
    return make_csv(50)


@pytest.fixture
def sample_tsv(tmp_path) -> Path:
    """A small tab-separated file with a header."""
    path = tmp_path / "data.tsv"
    path.write_text("a\tb\tc\n1\t2\t3\n4\t5\t6\n")
    return path


@pytest.fixture
def text_file(tmp_path) -> Path:
    """A small Python source file."""
    path = tmp_path / "sample.py"
    path.write_text("x = 1\n\n\ndef main():\n    return x\n")
    return path
