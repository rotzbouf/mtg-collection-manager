"""Deck builder route."""
from __future__ import annotations

import asyncio
import os

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

import ui.deps as deps
from core.deckbuilder import (
    THEMES,
    build_commander_deck,
    build_60_deck,
    format_commander_decklist,
    format_60_decklist,
    rank_commanders,
)

_TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
templates = Jinja2Templates(directory=_TEMPLATES_DIR)

router = APIRouter()

COLORS = [
    ("W", "White"),
    ("U", "Blue"),
    ("B", "Black"),
    ("R", "Red"),
    ("G", "Green"),
]

FORMATS = [
    ("commander", "Commander (100 cards)"),
    ("timeless", "Timeless (60 cards)"),
    ("standard", "Standard (60 cards)"),
]


@router.get("/deck", response_class=HTMLResponse)
async def deck_form(request: Request):
    return templates.TemplateResponse(request, "deck.html", {
        "colors": COLORS,
        "themes": list(THEMES.keys()),
        "formats": FORMATS,
        "result": None,
        "decklist": None,
        "error": None,
    })


@router.post("/deck", response_class=HTMLResponse)
async def deck_build(
    request: Request,
    fmt: str = Form("commander"),
    commander_name: str = Form(""),
):
    pool = await deps.db.get_all()

    error = None
    result = None
    decklist = None

    try:
        if fmt == "commander":
            # Find commander by name or pick best
            commander = None
            if commander_name.strip():
                name_lower = commander_name.strip().lower()
                for card in pool:
                    if (card.get("name_en") or "").lower() == name_lower:
                        commander = card
                        break
                if not commander:
                    error = f"Commander not found in collection: {commander_name!r}"
            if not commander and not error:
                ranked = await asyncio.to_thread(rank_commanders, pool)
                if not ranked:
                    error = "No eligible commander found in collection."
                else:
                    commander = ranked[0][0]

            if commander and not error:
                result = await asyncio.to_thread(build_commander_deck, commander, pool)
                decklist = await asyncio.to_thread(format_commander_decklist, result)
        else:
            if fmt not in ("timeless", "standard"):
                fmt = "timeless"
            result = await asyncio.to_thread(build_60_deck, pool, fmt)
            decklist = await asyncio.to_thread(format_60_decklist, result)
    except Exception as exc:
        error = str(exc)

    return templates.TemplateResponse(request, "deck.html", {
        "colors": COLORS,
        "themes": list(THEMES.keys()),
        "formats": FORMATS,
        "fmt": fmt,
        "commander_name": commander_name,
        "result": result,
        "decklist": decklist,
        "error": error,
    })
