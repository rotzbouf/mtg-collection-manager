"""Utility helpers for the desktop application."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

from PyQt6.QtGui import QPixmap, QIcon, QPainter
from PyQt6.QtCore import Qt
from PyQt6.QtSvg import QSvgRenderer

_MANA_DIR = Path(__file__).parent.parent / "images" / "mana"
_WUBRG_ORDER = {c: i for i, c in enumerate("WUBRGC")}
_COLOR_ICON_SIZE = 16  # px per symbol in combo-box rows

LANG_FLAGS: dict[str, str] = {
    "en":  "GB",
    "de":  "DE",
    "fr":  "FR",
    "it":  "IT",
    "es":  "ES",
    "pt":  "PT",
    "ja":  "JP",
    "ko":  "KR",
    "ru":  "RU",
    "zhs": "CN",
    "zht": "TW",
}

CONDITIONS = ["NM", "LP", "MP", "HP", "DMG"]

SORT_OPTIONS = [
    ("chaos",  "Chaos (color/type)"),
    ("name",   "Name"),
    ("set",    "Set / CN"),
    ("cmc",    "CMC"),
    ("added",  "Recently added"),
]

RARITY_COLORS: dict[str, str] = {
    "common":   "#aaaaaa",
    "uncommon": "#8cbfcf",
    "rare":     "#c8a951",
    "mythic":   "#e05c1a",
}


def color_identity_icon(colors: list[str]) -> Optional[QIcon]:
    """Return a QIcon with color-identity mana symbols stitched side by side.

    Colors are sorted in WUBRG order.  Returns None if no SVGs are found.
    """
    ordered = sorted(colors, key=lambda c: _WUBRG_ORDER.get(c.upper(), 99))
    s = _COLOR_ICON_SIZE
    pixmaps: list[QPixmap] = []
    for c in ordered:
        path = _MANA_DIR / f"{c.upper()}.svg"
        if not path.exists():
            continue
        renderer = QSvgRenderer(str(path))
        px = QPixmap(s, s)
        px.fill(Qt.GlobalColor.transparent)
        p = QPainter(px)
        renderer.render(p)
        p.end()
        pixmaps.append(px)

    if not pixmaps:
        return None

    total_w = s * len(pixmaps)
    composite = QPixmap(total_w, s)
    composite.fill(Qt.GlobalColor.transparent)
    p = QPainter(composite)
    for i, px in enumerate(pixmaps):
        p.drawPixmap(i * s, 0, px)
    p.end()
    return QIcon(composite)


def display_name(card: dict) -> str:
    """Return the best human-readable name for a card.

    For non-EN cards uses printed_name / name_de and appends name_en in
    parentheses when it differs.
    """
    lang = (card.get("language") or "en").lower()
    name_en = card.get("name_en") or ""
    if lang != "en":
        loc = card.get("printed_name") or card.get("name_de")
        if loc and loc != name_en:
            return f"{loc} ({name_en})"
        flag = lang.upper()
        return f"{name_en}  [{flag}]" if name_en else name_en
    return name_en


def lang_flag(card: dict) -> str:
    """Return a short country-code string for the card language."""
    lang = (card.get("language") or "en").lower()
    return LANG_FLAGS.get(lang, lang.upper())


def format_price(value) -> str:
    """Format a price value as '€1.23' or '—' if absent."""
    if value is None:
        return "—"
    try:
        return f"€{float(value):.2f}"
    except (TypeError, ValueError):
        return "—"


def load_pixmap(path: Path | str) -> Optional[QPixmap]:
    """Load a QPixmap from a local file path, or None on failure."""
    if path is None:
        return None
    p = QPixmap(str(path))
    if p.isNull():
        return None
    return p


def scale_pixmap(pixmap: QPixmap, w: int = 280, h: int = 390) -> QPixmap:
    """Scale a pixmap to fit w×h while preserving aspect ratio."""
    return pixmap.scaled(
        w, h,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


async def async_pixmap(
    scryfall_id: Optional[str],
    image_url: Optional[str],
) -> Optional[QPixmap]:
    """Return a QPixmap for the given card, downloading if necessary."""
    if not scryfall_id:
        return None

    from core.image_cache import get_cached_path, ensure_cached

    path = await asyncio.to_thread(get_cached_path, scryfall_id)
    if path is None and image_url:
        path = await ensure_cached(scryfall_id, image_url)

    if path is None:
        return None

    pixmap = await asyncio.to_thread(load_pixmap, path)
    return pixmap
