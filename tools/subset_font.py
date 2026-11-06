"""Shrink M PLUS Rounded 1c to the glyphs the game draws (Japanese UI text, kana, ASCII).

    pip install fonttools
    python tools/subset_font.py <folder with MPLUSRounded1c-Regular.ttf and -Bold.ttf>

Source fonts (SIL OFL 1.1): https://github.com/google/fonts/tree/main/ofl/mplusrounded1c
Re-run after adding Japanese text to leakbots/gui/i18n.py; tests/test_i18n.py catches misses.
"""

from __future__ import annotations

import sys
from pathlib import Path

from fontTools import subset

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from leakbots.gui.i18n import all_japanese_text  # noqa: E402
from leakbots.gui.theme import JP_FONT_FILES  # noqa: E402

EXTRA = "日本語·−—…×〜↑↓「」、。:()!?"


def charset() -> str:
    ascii_printable = "".join(chr(c) for c in range(0x20, 0x7F))
    kana = "".join(chr(c) for c in range(0x3040, 0x3100))  # hiragana + katakana, for bot names
    fullwidth = "".join(chr(c) for c in range(0xFF01, 0xFF5F))
    return "".join(sorted(set(ascii_printable + kana + fullwidth + EXTRA + all_japanese_text())))


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    src_dir = Path(sys.argv[1])
    text = charset()
    for weight, dest in JP_FONT_FILES.items():
        src = src_dir / f"MPLUSRounded1c-{weight}.ttf"
        opts = subset.Options()
        opts.name_IDs = ["*"]  # keep copyright + license strings in the font
        font = subset.load_font(str(src), opts)
        subsetter = subset.Subsetter(opts)
        subsetter.populate(text=text)
        subsetter.subset(font)
        dest.parent.mkdir(parents=True, exist_ok=True)
        subset.save_font(font, str(dest), opts)
        print(f"{dest.relative_to(ROOT)}: {len(text)} chars, {dest.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
