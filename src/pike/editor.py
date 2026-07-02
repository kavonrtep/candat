"""Editor buffer widget: a TextArea bound to an optional file on disk."""

from __future__ import annotations

from pathlib import Path

from textual.widgets import TextArea

LANGUAGES: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".md": "markdown",
    ".markdown": "markdown",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
    ".html": "html",
    ".htm": "html",
    ".xml": "xml",
    ".css": "css",
    ".tcss": "css",
    ".toml": "toml",
    ".js": "javascript",
    ".sql": "sql",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
}


def language_for(path: Path | None) -> str | None:
    if path is None:
        return None
    return LANGUAGES.get(path.suffix.lower())


class EditorBuffer(TextArea):
    """A text editing buffer, optionally backed by a file."""

    def __init__(self, path: Path | None = None, text: str = "", **kwargs) -> None:
        super().__init__(
            text,
            language=None,
            theme="github_light",
            show_line_numbers=True,
            soft_wrap=False,
            tab_behavior="indent",
            **kwargs,
        )
        self.path = path
        self.modified = False
        self._saved_text = text
        self._apply_language()

    @property
    def display_name(self) -> str:
        return self.path.name if self.path else "*untitled*"

    def _apply_language(self) -> None:
        language = language_for(self.path)
        if language in self.available_languages:
            self.language = language

    def load(self, path: Path) -> None:
        self.path = path
        self.text = path.read_text()
        self.modified = False
        self._saved_text = self.text
        self._apply_language()

    def save(self, path: Path | None = None) -> Path:
        """Write the buffer to disk; returns the path written."""
        if path is not None:
            self.path = path
            self._apply_language()
        if self.path is None:
            raise ValueError("buffer has no file name")
        self.path.write_text(self.text)
        self.modified = False
        self._saved_text = self.text
        return self.path

    def _on_text_area_changed(self, event: TextArea.Changed) -> None:
        # Runs before the message bubbles to the app, so the app sees the
        # up-to-date modified state. A buffer whose text matches what is on
        # disk is not modified, even after a programmatic load().
        self.modified = self.text != self._saved_text
