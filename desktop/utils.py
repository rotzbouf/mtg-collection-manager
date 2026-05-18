"""Utility helpers for the desktop application."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

from PyQt6.QtGui import QPixmap
from PyQt6.QtCore import Qt

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


def display_name(card: dict) -> str:
    """Return the best human-readable name for a card.

    For non-EN cards uses printed_name / name_de and appends name_en in
    parentheses when it differs.
    """
    lang = (card.get("language") or "en").lower()
    name_en = card.get("name_en") or ""
    if lang != "en":
        loc = card.get("printed_name") or card.get("name_de") or name_en
        if loc and loc != name_en:
            return f"{loc} ({name_en})"
        return loc or name_en
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
