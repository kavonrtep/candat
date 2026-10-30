"""The candat-light theme: near-white background, true-black text, high contrast.

Based on Konsole's BlackOnWhite scheme with GitHub-light accent colors,
which keep WCAG-AA contrast on a white background.
"""

from textual.theme import Theme

CANDAT_LIGHT = Theme(
    name="candat-light",
    dark=False,
    primary="#0550ae",
    secondary="#116329",
    accent="#8250df",
    warning="#953800",
    error="#cf222e",
    success="#116329",
    foreground="#000000",
    background="#ffffff",
    surface="#f6f6f6",
    panel="#ececec",
    variables={
        "footer-key-foreground": "#0550ae",
        "input-selection-background": "#0550ae 30%",
        "block-cursor-foreground": "#ffffff",
        "block-cursor-background": "#0550ae",
    },
)


# -- editor (TextArea) theme ---------------------------------------------------

# Fenced code blocks in raw markdown sit on this tint, full width; inline
# code spans get the same tint so `code` reads as code in prose too.
CODE_BLOCK_BG = "#e3e8ef"


def editor_theme():
    """github_light plus markdown readability styles (built lazily: the
    TextArea theme module is a Textual internal)."""
    from dataclasses import replace

    from rich.style import Style
    from textual._text_area_theme import TextAreaTheme

    base = TextAreaTheme.get_builtin_theme("github_light")
    styles = dict(base.syntax_styles)
    styles.update(
        {
            "inline_code": Style(color="#0a3069", bgcolor=CODE_BLOCK_BG),
            "fence.delimiter": Style(color="#8c959f"),
            "fence.info": Style(color="#8250df", bold=True),
            # github_light leaves these unstyled or near-invisible grey.
            "function.call": Style(color="#6639bb"),
            "type": Style(color="#116329"),
            "type.class": Style(color="#116329"),
            "heading": Style(color="#0550ae", bold=True),
            "heading.marker": Style(color="#8c959f", bold=True),
            "link.label": Style(color="#0550ae"),
            "link.uri": Style(color="#57606a", underline=True),
            "list.marker": Style(color="#953800", bold=True),
        }
    )
    return replace(base, name="candat_light", syntax_styles=styles)
