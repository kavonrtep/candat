"""Regenerate the welcome-screen fish (candat.welcome.FISH).

Converts the line-art zander in docs/candat-fish.png to braille unicode
(2x4 dots per character). Line art needs max-pooling rather than average
downsampling — a dot is inked if *any* source pixel under it is inked —
or the thin outlines vanish (binarize, invert, BOX-resize, low threshold).

Usage (Pillow required):
    uv run --with pillow python docs/make_fish.py [cols rows sensitivity]

Paste the output into FISH in src/candat/welcome.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

SOURCE = Path(__file__).parent / "candat-fish.png"
DOT_BITS = (
    (0, 0, 0x01), (1, 0, 0x02), (2, 0, 0x04), (0, 1, 0x08),
    (1, 1, 0x10), (2, 1, 0x20), (3, 0, 0x40), (3, 1, 0x80),
)


def braille(cols: int, rows: int, sensitivity: int) -> str:
    image = Image.open(SOURCE).convert("L")
    inked = image.point(lambda v: 255 if v < 128 else 0)  # ink -> white
    small = inked.resize((cols * 2, rows * 4), Image.BOX)
    bitmap = small.point(lambda v: 1 if v >= sensitivity else 0).load()
    lines = []
    for cy in range(rows):
        cells = []
        for cx in range(cols):
            code = 0
            for dy, dx, bit in DOT_BITS:
                if bitmap[cx * 2 + dx, cy * 4 + dy]:
                    code |= bit
            cells.append(chr(0x2800 + code) if code else " ")
        lines.append("".join(cells).rstrip())
    return "\n".join(lines).strip("\n")


if __name__ == "__main__":
    cols, rows, sensitivity = (
        [int(a) for a in sys.argv[1:4]] if len(sys.argv) > 3 else (60, 15, 40)
    )
    print(braille(cols, rows, sensitivity))
