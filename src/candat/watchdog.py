"""Opt-in stall watchdog for diagnosing UI freezes.

Run candat with ``CANDAT_WATCHDOG=1`` (or a number of seconds, e.g.
``CANDAT_WATCHDOG=0.3``) and a background thread keeps pinging the event
loop. When a ping goes unanswered for longer than the threshold, the stack
of the event-loop thread is appended to
``~/.local/state/candat/stalls.log`` — one entry per stall, written while
the stall is still happening, so the log shows exactly what blocked the UI.
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
import traceback
from pathlib import Path

DEFAULT_THRESHOLD = 0.5
PING_INTERVAL = 0.05


def log_path() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "candat" / "stalls.log"


def threshold_from_env() -> float | None:
    """The stall threshold in seconds, or None when the watchdog is off."""
    value = os.environ.get("CANDAT_WATCHDOG", "").strip()
    if not value or value == "0":
        return None
    try:
        seconds = float(value)
    except ValueError:
        return DEFAULT_THRESHOLD
    return seconds if seconds > 0 and value != "1" else DEFAULT_THRESHOLD


class Watchdog:
    def __init__(self, loop: asyncio.AbstractEventLoop, threshold: float) -> None:
        self._loop = loop
        self._threshold = threshold
        self._thread_id = threading.get_ident()  # created on the loop thread
        self._last_beat = time.monotonic()
        self._stop = threading.Event()
        self.stalls = 0
        threading.Thread(target=self._run, daemon=True, name="watchdog").start()

    def stop(self) -> None:
        self._stop.set()

    def _beat(self) -> None:
        self._last_beat = time.monotonic()

    def _run(self) -> None:
        reported = False
        while not self._stop.wait(PING_INTERVAL):
            try:
                self._loop.call_soon_threadsafe(self._beat)
            except RuntimeError:  # loop closed
                return
            late = time.monotonic() - self._last_beat
            if late > self._threshold and not reported:
                reported = True
                self.stalls += 1
                self._report(late)
            elif late <= self._threshold:
                reported = False

    def _report(self, late: float) -> None:
        frame = sys._current_frames().get(self._thread_id)
        stack = "".join(traceback.format_stack(frame)) if frame else "(no frame)\n"
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        entry = f"--- {stamp}  UI blocked for >{late:.2f}s; event-loop stack:\n{stack}\n"
        try:
            path = log_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(entry)
        except OSError:
            pass


def start_from_env() -> Watchdog | None:
    """Start the watchdog if CANDAT_WATCHDOG asks for it (call from the
    running event loop)."""
    threshold = threshold_from_env()
    if threshold is None:
        return None
    return Watchdog(asyncio.get_running_loop(), threshold)
