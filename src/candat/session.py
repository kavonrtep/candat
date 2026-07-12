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
