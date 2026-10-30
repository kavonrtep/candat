"""Best-effort mirroring of copies to the system clipboard.

Two channels, both fire-and-forget (the tool runs on a background thread):

- The OSC 52 escape sequence through the terminal (Textual's
  ``App.copy_to_clipboard``). Works in any terminal that supports it —
  including over SSH, where the escape travels back to the *local*
  terminal, which performs the copy. Inside tmux it needs
  ``set -g set-clipboard on``.
- A local clipboard tool (``wl-copy`` / ``xclip`` / ``xsel`` / ``pbcopy``)
  when one is installed and a display is available — covers terminals
  without OSC 52, but only on the machine candat runs on.

Neither channel can report success (terminals silently drop OSC 52 they
don't support), so both are tried.

The ``system_clipboard`` config key picks when this fires: ``"copy"``
(default) mirrors explicit copies — ``M-w`` and ``w`` in the file tree;
``"all"`` mirrors every kill-ring entry (emacs' select-enable-clipboard
behaviour); ``"off"`` keeps everything app-local.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading

from . import config

MODES = ("copy", "all", "off")


def mode() -> str:
    value = str(config.load()["system_clipboard"])
    return value if value in MODES else "copy"


def copy_explicit(app, text: str) -> None:
    """An explicit copy command (M-w, `w` in the tree). In "all" mode the
    kill-ring hook has already mirrored the push, so only "copy" acts here."""
    if text and mode() == "copy":
        _copy(app, text)


def copy_on_kill(app, text: str) -> None:
    """Every kill-ring push lands here; mirrors only in "all" mode."""
    if text and mode() == "all":
        _copy(app, text)


def _copy(app, text: str) -> None:
    app.copy_to_clipboard(text)  # OSC 52 (also recorded on app.clipboard)
    _mirror_in_background(text)


# The tool runs on one background thread: a slow or hung clipboard tool
# must not stall the key that copied. Latest wins — copies made while the
# tool is busy collapse into the newest one.
_slot_lock = threading.Lock()
_slot: list[str] = []  # at most one waiting text
_worker_running = False


def _mirror_in_background(text: str) -> None:
    global _worker_running
    with _slot_lock:
        _slot[:] = [text]
        if _worker_running:
            return
        _worker_running = True
    threading.Thread(target=_mirror_worker, daemon=True, name="clipboard").start()


def _mirror_worker() -> None:
    global _worker_running
    while True:
        with _slot_lock:
            if not _slot:
                _worker_running = False
                return
            text = _slot.pop()
        _copy_via_tool(text)


def _tool_command() -> list[str] | None:
    if os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-copy"):
        return ["wl-copy"]
    if os.environ.get("DISPLAY"):
        if shutil.which("xclip"):
            return ["xclip", "-selection", "clipboard"]
        if shutil.which("xsel"):
            return ["xsel", "--clipboard", "--input"]
    if shutil.which("pbcopy"):  # macOS
        return ["pbcopy"]
    return None


def _copy_via_tool(text: str) -> None:
    command = _tool_command()
    if command is None:
        return
    try:
        subprocess.run(
            command,
            input=text.encode(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=1,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        pass  # a failed mirror must never break the copy itself
