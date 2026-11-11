"""Post length counting.

``x_length`` follows X's published weighted-length rules (twitter-text v3):
every URL counts as 23, characters in the light ranges below count 1 and every
other code point (CJK, most emoji) counts 2. ``char_length`` is a plain
code-point count for platforms that do not weight characters.
"""

from __future__ import annotations

import re

URL_RE = re.compile(
    r"(?:https?://[^\s<>\"]+|(?<![@\w.])(?:[a-z0-9-]+\.)+(?:com|org|net|io|ai|app|dev|co|xyz|gg|me|tv|so|sh|ng|uk|us)(?:/[^\s<>\"]*)?)",
    re.IGNORECASE,
)

_LIGHT_RANGES = ((0, 4351), (8192, 8205), (8208, 8223), (8242, 8247))
URL_WEIGHT = 23


def find_urls(text: str) -> list[str]:
    return [m.group(0).rstrip(".,!?;:)") for m in URL_RE.finditer(text or "")]


def _char_weight(ch: str) -> int:
    cp = ord(ch)
    for lo, hi in _LIGHT_RANGES:
        if lo <= cp <= hi:
            return 1
    return 2


def x_length(text: str) -> int:
    text = text or ""
    total = 0
    pos = 0
    for m in URL_RE.finditer(text):
        total += sum(_char_weight(c) for c in text[pos:m.start()])
        total += URL_WEIGHT
        pos = m.end()
    total += sum(_char_weight(c) for c in text[pos:])
    return total


def char_length(text: str) -> int:
    return len(text or "")
