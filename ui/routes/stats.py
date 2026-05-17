"""Stats route."""
from __future__ import annotations

import os

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

import ui.deps as deps

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
    return templates.TemplateResponse("stats.html", {
        "request": request,
        "stats": data,
        "containers": containers,
        "lang_flags": LANG_FLAGS,
    })
