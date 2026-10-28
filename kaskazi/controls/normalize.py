"""Normalising text before any check reads it.

Hidden characters are a common way to smuggle instructions past filters and people: zero-width
characters, bidirectional overrides and the Unicode "tag" block can all carry text that a screen
doesn't show. They are removed, and the text is NFKC-normalised so look-alike forms (full-width
digits, ligatures) read as the plain characters they imitate.
"""

from __future__ import annotations

import re
import unicodedata

MAX_MESSAGE_CHARS = 2000


def _char_class(*ranges: tuple[int, int]) -> str:
    # Built from code points at run time, so this file itself holds no invisible characters.
    return "[" + "".join(f"{chr(lo)}-{chr(hi)}" for lo, hi in ranges) + "]"


_INVISIBLE = re.compile(
    _char_class(
        (0x00AD, 0x00AD),  # soft hyphen
        (0x180E, 0x180E),  # Mongolian vowel separator
        (0x200B, 0x200F),  # zero-width spaces, joiners, direction marks
        (0x202A, 0x202E),  # bidirectional embeddings and overrides
        (0x2060, 0x2064),  # word joiner, invisible operators
        (0x2066, 0x2069),  # bidirectional isolates
        (0xFEFF, 0xFEFF),  # byte order mark
        (0xE0000, 0xE007F),  # tag characters
    )
)
_SPACES = re.compile(
    _char_class(
        (0x0009, 0x0009),  # tab
        (0x0020, 0x0020),  # space
        (0x00A0, 0x00A0),  # no-break space
        (0x2000, 0x200A),  # en quad to hair space
        (0x202F, 0x202F),  # narrow no-break space
        (0x205F, 0x205F),  # medium mathematical space
        (0x3000, 0x3000),  # ideographic space
    )
    + "+"
)


def strip_invisible(text: str) -> tuple[str, int]:
    """Returns the text without invisible characters, and how many were removed."""

    cleaned, removed = _INVISIBLE.subn("", text)
    return cleaned, removed


def normalize(text: str, limit: int = MAX_MESSAGE_CHARS) -> tuple[str, int]:
    text, removed = strip_invisible(text)
    text = unicodedata.normalize("NFKC", text)
    text = "".join(ch for ch in text if ch in "\n\t" or unicodedata.category(ch)[0] != "C")
    text = _SPACES.sub(" ", text).strip()
    return text[:limit], removed
