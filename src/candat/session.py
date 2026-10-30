"""Session persistence: reopen the files you had open, per project root.

Sessions live in ``~/.local/state/candat/sessions.json`` (XDG aware), keyed
by the resolved project root (the directory candat was started in / on). A
session records the open files in tab order, the active one, and each
buffer's cursor and scroll. Only the most recent ``MAX_ROOTS`` projects are
kept.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

MAX_ROOTS = 50


def state_path() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "candat" / "sessions.json"


def _load_all() -> dict:
    try:
        data = json.loads(state_path().read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load(root: Path) -> dict | None:
    """The saved session for this project root, or None.

    Shape: {"files": [{"path", "row", "col", "scroll"}, ...], "active": path}
    """
    session = _load_all().get(str(root.resolve()))
    return session if isinstance(session, dict) else None


def save(root: Path, files: list[dict], active: str | None) -> None:
    """Persist this root's session (most-recently-used last; oldest roots
    are dropped beyond MAX_ROOTS). Never raises."""
    sessions = _load_all()
    key = str(root.resolve())
    sessions.pop(key, None)
    if files:
        sessions[key] = {"files": files, "active": active}
    while len(sessions) > MAX_ROOTS:
        sessions.pop(next(iter(sessions)))
    path = state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(sessions, indent=1))
    except OSError:
        pass


# -- markdown view mode ------------------------------------------------------

# Remembered per file (absolute path) plus the last mode picked anywhere,
# which new markdown files open in. Kept in its own file next to sessions.
MAX_VIEW_FILES = 500
VIEW_MODES = ("split", "only", "off")


def views_path() -> Path:
    return state_path().with_name("views.json")


def _load_views() -> dict:
    try:
        data = json.loads(views_path().read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def view_mode(path: Path | None, default: str = "split") -> str:
    """The markdown view to open `path` in: its own last mode, else the
    last mode used for any file, else `default`."""
    data = _load_views()
    files = data.get("files")
    if path is not None and isinstance(files, dict):
        mode = files.get(str(path.resolve()))
        if mode in VIEW_MODES:
            return mode
    mode = data.get("last")
    return mode if mode in VIEW_MODES else default


def save_view_mode(path: Path | None, mode: str) -> None:
    """Remember `mode` for `path` and as the default for new files. Never
    raises."""
    if mode not in VIEW_MODES:
        return
    data = _load_views()
    files = data.get("files")
    if not isinstance(files, dict):
        files = {}
    if path is not None:
        key = str(path.resolve())
        files.pop(key, None)
        files[key] = mode
        while len(files) > MAX_VIEW_FILES:
            files.pop(next(iter(files)))
    target = views_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"last": mode, "files": files}, indent=1))
    except OSError:
        pass
