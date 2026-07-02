"""The emacs kill ring: a bounded ring of killed text, shared by all buffers."""

from __future__ import annotations


class KillRing:
    def __init__(self, max_size: int = 60) -> None:
        self._items: list[str] = []
        self._yank_index = 0
        self._max_size = max_size

    def __len__(self) -> int:
        return len(self._items)

    def push(self, text: str) -> None:
        """Start a new kill-ring entry and point the yank cursor at it."""
        if not text:
            return
        self._items.insert(0, text)
        del self._items[self._max_size :]
        self._yank_index = 0

    def add_to_top(self, text: str, *, before: bool = False) -> None:
        """Grow the newest entry (consecutive kills accumulate, as in emacs);
        backward kills prepend."""
        if not self._items:
            self.push(text)
            return
        self._items[0] = text + self._items[0] if before else self._items[0] + text
        self._yank_index = 0

    @property
    def current(self) -> str | None:
        """The entry the yank cursor points at (what C-y inserts)."""
        return self._items[self._yank_index] if self._items else None

    def rotate(self) -> str | None:
        """Advance the yank cursor to the next-older entry (M-y)."""
        if not self._items:
            return None
        self._yank_index = (self._yank_index + 1) % len(self._items)
        return self.current
