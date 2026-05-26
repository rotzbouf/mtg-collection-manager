"""Stats route."""
from __future__ import annotations

import os

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

import server.ui.deps as deps

_TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
templates = Jinja2Templates(directory=_TEMPLATES_DIR)

router = APIRouter()

LANG_FLAGS = {
    "en": "🇬🇧", "de": "🇩🇪", "fr": "🇫🇷", "it": "🇮🇹",
    "ja": "🇯🇵", "ko": "🇰🇷", "ru": "🇷🇺", "pt": "🇵🇹",
    "zh": "🇨🇳", "zhs": "🇨🇳", "zht": "🇨🇳", "es": "🇪🇸",
}


@router.get("/stats", response_class=HTMLResponse)
async def stats_page(request: Request):
    data = await deps.db.stats()
    containers = await deps.db.container_stats()
    sets = await deps.db.get_sets_summary()
    # Top 10 sets by distinct card names owned (most varied first)
    top_sets = sorted(sets, key=lambda s: s.get("distinct_names") or 0, reverse=True)[:10]
    return templates.TemplateResponse(request, "stats.html", {
        "stats": data,
        "containers": containers,
        "lang_flags": LANG_FLAGS,
        "sets_count": len(sets),
        "top_sets": top_sets,
    })
