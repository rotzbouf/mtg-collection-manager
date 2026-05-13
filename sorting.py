"""
Chaos sorting for MTG cards.

Order: White → Blue → Black → Red → Green → Multicolor → Colorless/Artifact → Land
Within each color: Creature → Instant → Sorcery → Enchantment → Artifact → Planeswalker → Other
Within each type: ascending CMC, then alphabetical name
"""

import json
from typing import Union

_COLOR_BUCKET = {"W": 0, "U": 1, "B": 2, "R": 3, "G": 4}

_TYPE_BUCKET = [
    "Creature",
    "Instant",
    "Sorcery",
    "Enchantment",
    "Artifact",
    "Planeswalker",
    "Battle",
    "Tribal",
]


def _color_bucket(colors: list[str], type_line: str) -> int:
    if "Land" in type_line:
        return 7
    if not colors:
        return 6  # colorless / artifact
    if len(colors) > 1:
        return 5  # multicolor
    return _COLOR_BUCKET.get(colors[0], 6)


def _type_bucket(type_line: str) -> int:
    for i, t in enumerate(_TYPE_BUCKET):
        if t in type_line:
            return i
    return len(_TYPE_BUCKET)


def compute_chaos_key(
    colors: Union[list[str], str],
    type_line: str,
    cmc: float,
    name: str,
) -> str:
    """Return a zero-padded string key suitable for ORDER BY in SQLite."""
    if isinstance(colors, str):
        try:
            colors = json.loads(colors)
        except Exception:
            colors = [c.strip() for c in colors.split(",") if c.strip()]

    cb = _color_bucket(colors, type_line)
    tb = _type_bucket(type_line)
    return f"{cb:01d}_{tb:02d}_{int(cmc):03d}_{name.lower()}"


def color_sort_order(colors: Union[list[str], str], type_line: str) -> int:
    if isinstance(colors, str):
        try:
            colors = json.loads(colors)
        except Exception:
            colors = [c.strip() for c in colors.split(",") if c.strip()]
    return _color_bucket(colors, type_line)


def type_sort_order(type_line: str) -> int:
    return _type_bucket(type_line)
