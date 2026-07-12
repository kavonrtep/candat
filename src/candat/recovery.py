"""Autosave / crash recovery for buffers with unsaved edits.

Dirty buffers are snapshotted to ``~/.cache/candat/recovery/`` on a short
timer and again from the crash handler, so a hard crash, `SIGKILL`, or power
loss leaves a recent copy of your work on disk. A clean quit clears the
directory; on the next launch, any files still there are reported to the user
(candat never silently discards them, and never auto-overwrites the original).

Each snapshot filename encodes the original path so it can be matched up by
hand: the absolute path with ``/`` replaced by ``%``, plus a ``.txt`` suffix.
A companion ``.meta`` line records the real path and whether the buffer had a
filename at all.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def recovery_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "candat" / "recovery"


def _slug(path: Path | None, index: int) -> str:
    if path is None:
        return f"untitled-{index}"
    return str(path).replace("%", "%25").replace("/", "%").lstrip("%") or f"root-{index}"


def snapshot(buffers: list[tuple[Path | None, str]]) -> Path | None:
    """Write each (path, text) as a recovery file; return the directory used,
    or None if nothing was written. Never raises — recovery must not itself
    break saving or crashing."""
    try:
        directory = recovery_dir()
        directory.mkdir(parents=True, exist_ok=True)
    except (OSError, ValueError):
        return None
    written = False
    for index, (path, text) in enumerate(buffers):
        name = _slug(path, index)
        try:
            (directory / f"{name}.txt").write_text(text, encoding="utf-8")
            (directory / f"{name}.meta").write_text(
                json.dumps({"path": str(path) if path else None}),
                encoding="utf-8",
            )
            written = True
        except OSError:
            continue
    return directory if written else None


def clear() -> None:
    """Drop all recovery files (called on a clean quit). Never raises."""
    directory = recovery_dir()
    try:
        entries = list(directory.iterdir())
    except OSError:
        return
    for entry in entries:
        try:
            entry.unlink()
        except OSError:
            pass


def pending() -> list[Path]:
    """Recovery snapshots left behind by a previous crash (the `.txt` files)."""
    directory = recovery_dir()
    try:
        return sorted(p for p in directory.iterdir() if p.suffix == ".txt")
    except OSError:
        return []
