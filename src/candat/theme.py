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
