"""User configuration: ``~/.config/candat/config.toml`` (XDG aware).

A flat TOML file of simple scalars. Unknown keys are ignored; values of the
wrong type fall back to the default, so a typo can't break startup.

Recognised keys:

- ``tree_icons``: file-tree icon set — "emoji", "nerd" or "ascii"
  (the ``CANDAT_TREE_ICONS`` environment variable overrides this)
- ``tree_width``: file-tree panel width in cells (``C-x {`` / ``C-x }`` and
  the drag splitter save their result here)
- ``pager_wrap``: whether the large-file pager starts with soft wrap on
- ``tabstop``: tab width in the pager
- ``restore_session``: reopen the previous session's files when candat is
  started without file arguments
- ``table_suffixes``: file suffixes that open straight into the table viewer
  (any other buffer can still be switched to a table with ``C-c C-v``)
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10: tomllib landed in 3.11
    import tomli as tomllib  # type: ignore[no-redef]

DEFAULTS: dict[str, object] = {
    "tree_icons": "emoji",
    "tree_width": 32,
    "pager_wrap": False,
    "tabstop": 8,
    "restore_session": True,
    "table_suffixes": [".csv", ".tsv"],
}


def config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "candat" / "config.toml"


def load() -> dict[str, object]:
    """The user's settings merged over the defaults. Never raises."""
    merged = dict(DEFAULTS)
    try:
        with config_path().open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return merged
    for key, default in DEFAULTS.items():
        value = data.get(key)
        # Exact type match (bool is an int subclass, so isinstance won't do).
        if value is not None and type(value) is type(default):
            merged[key] = value
    return merged


def save_setting(key: str, value: object) -> Path | None:
    """Persist one top-level key, keeping the rest of the file (including
    comments on other lines) intact. Returns the path, or None on failure."""
    if isinstance(value, bool):
        rendered = "true" if value else "false"
    elif isinstance(value, str):
        rendered = f'"{value}"'
    else:
        rendered = str(value)
    path = config_path()
    try:
        lines = path.read_text().splitlines()
    except OSError:
        lines = []
    replaced = False
    for i, line in enumerate(lines):
        if line.split("=", 1)[0].strip() == key:
            lines[i] = f"{key} = {rendered}"
            replaced = True
            break
    if not replaced:
        lines.append(f"{key} = {rendered}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n")
    except OSError:
        return None
    return path
