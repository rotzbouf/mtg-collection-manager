"""Set completion tracker routes."""
from __future__ import annotations

import os
import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

import server.ui.deps as deps

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
templates = Jinja2Templates(directory=_TEMPLATES_DIR)

router = APIRouter()

_SCRYFALL_SET_URL = "https://api.scryfall.com/sets/{}"


async def _fetch_scryfall_set(set_code: str) -> dict | None:
    """Fetch set metadata from Scryfall /sets/{code}."""
    url = _SCRYFALL_SET_URL.format(set_code.lower())
    try:
        data = await deps.scryfall._get(url)
        return data
    except Exception as exc:
        logger.warning("Scryfall set fetch failed for %s: %s", set_code, exc)
        return None


def _rarity_order(r: str) -> int:
    return {"mythic": 0, "rare": 1, "uncommon": 2, "common": 3}.get(r.lower(), 4)


@router.get("/sets", response_class=HTMLResponse)
async def sets_list(request: Request):
    sets = await deps.db.get_sets_summary()
    return templates.TemplateResponse(request, "sets/list.html", {
        "sets": sets,
    })


@router.get("/sets/{set_code}", response_class=HTMLResponse)
async def set_detail(request: Request, set_code: str):
    # Sanitize set_code
    set_code = set_code.lower().strip()[:8]

    cards = await deps.db.get_collection_by_set(set_code)

    if not cards:
        return templates.TemplateResponse(request, "sets/detail.html", {
            "set_code": set_code,
            "set_name": set_code.upper(),
            "cards": [],
            "scryfall_meta": None,
            "owned_distinct": 0,
            "total_set_size": None,
            "completion_pct": None,
            "total_value_eur": 0.0,
        }, status_code=200)

    # Aggregate by name to get distinct cards
    seen: dict[str, dict] = {}
    for c in cards:
        key = c.get("name_en") or c.get("printed_name") or ""
        if key and key not in seen:
            seen[key] = c

    owned_distinct = len(seen)
    set_name = cards[0].get("set_name") or set_code.upper()
    total_value_eur = sum(c.get("price_eur") or 0.0 for c in cards)

    # Fetch Scryfall set metadata for total card count
    scryfall_meta = await _fetch_scryfall_set(set_code)
    total_set_size: int | None = None
    completion_pct: float | None = None
    if scryfall_meta:
        total_set_size = scryfall_meta.get("card_count")
        if total_set_size and total_set_size > 0:
            completion_pct = round(owned_distinct / total_set_size * 100, 1)

    # Group cards by rarity for display
    by_rarity: dict[str, list[dict]] = {}
    for c in cards:
        r = (c.get("rarity") or "unknown").lower()
        by_rarity.setdefault(r, []).append(c)
    # Sort rarity groups
    sorted_rarities = sorted(by_rarity.keys(), key=_rarity_order)

    return templates.TemplateResponse(request, "sets/detail.html", {
        "set_code": set_code,
        "set_name": set_name,
        "cards": cards,
        "by_rarity": {r: by_rarity[r] for r in sorted_rarities},
        "scryfall_meta": scryfall_meta,
        "owned_distinct": owned_distinct,
        "total_set_size": total_set_size,
        "completion_pct": completion_pct,
        "total_value_eur": round(total_value_eur, 2),
    })
