"""Editor groups ("windows") for split layouts.

Each EditorGroup is a tab group of buffers. The app arranges one or more of
them side by side (C-x 3) or stacked (C-x 2); C-x 0 closes one, C-x 1 keeps
only the current, C-x o moves between them. A buffer lives in exactly one
group — Textual's editor widget is its own view, so the same buffer cannot
be shown in two groups at once.
"""

from __future__ import annotations

from textual.widget import Widget
from textual.widgets import TabbedContent

MAX_GROUPS = 4


class EditorGroup(TabbedContent):
    """One split window: a TabbedContent holding this window's buffers."""


def group_of(widget: Widget | None) -> EditorGroup | None:
    """The EditorGroup containing a widget, or None."""
    if widget is None:
        return None
    for ancestor in widget.ancestors_with_self:
        if isinstance(ancestor, EditorGroup):
            return ancestor
    return None
