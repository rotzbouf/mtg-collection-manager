"""Container routes."""
from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import server.ui.deps as deps
from server.ui.routes.collection import SORT_OPTIONS
from server.ui.csrf import verify_csrf

_TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
templates = Jinja2Templates(directory=_TEMPLATES_DIR)

router = APIRouter()

PAGE_SIZE = 25

# Must match BUILTIN_TYPES in core/config.py
CONTAINER_TYPES = ["binder", "box", "deck", "commander", "overcount", "trade", "other"]

# Types that can carry a competitive format tag
DECK_TYPES = {"deck", "commander"}

DECK_FORMATS = [
    ("",           "— no format —"),
    ("commander",  "⚔  Commander / EDH"),
    ("modern",     "Modern"),
    ("pioneer",    "Pioneer"),
    ("standard",   "Standard"),
    ("legacy",     "Legacy"),
    ("vintage",    "Vintage"),
    ("pauper",     "Pauper"),
    ("timeless",   "Timeless"),
    ("historic",   "Historic"),
]

_VALID_FORMATS = {code for code, _ in DECK_FORMATS if code}

LANG_FLAGS = {
    "en": "🇬🇧", "de": "🇩🇪", "fr": "🇫🇷", "it": "🇮🇹",
    "ja": "🇯🇵", "ko": "🇰🇷", "ru": "🇷🇺", "pt": "🇵🇹",
    "zh": "🇨🇳", "zhs": "🇨🇳", "zht": "🇨🇳", "es": "🇪🇸",
}


def _display_name(card: dict) -> str:
    lang = card.get("language", "en")
    en = card.get("name_en") or ""
    if lang == "de":
        loc = card.get("name_de") or card.get("printed_name") or en
    else:
        loc = card.get("printed_name") or en
    if loc and loc != en:
        return f"{loc} ({en})"
    return en


def _format_label(code: Optional[str]) -> str:
    if not code:
        return ""
    return next((lbl for c, lbl in DECK_FORMATS if c == code), code.capitalize())


@router.get("/containers", response_class=HTMLResponse)
async def containers_list(request: Request):
    containers = await deps.db.list_containers()
    for ct in containers:
        ct["_format_label"] = _format_label(ct.get("deck_format"))
    return templates.TemplateResponse(request, "containers/list.html", {
        "containers": containers,
        "container_types": CONTAINER_TYPES,
        "deck_formats": DECK_FORMATS,
        "deck_types": list(DECK_TYPES),
    })


@router.get("/containers/{container_id}", response_class=HTMLResponse)
async def container_detail(
    request: Request,
    container_id: int,
    page: int = 1,
    sort: str = "chaos",
):
    container = await deps.db.get_container(container_id)
    if not container:
        return HTMLResponse("<h2>Container not found</h2>", status_code=404)

    valid_sorts = {k for k, _ in SORT_OPTIONS}
    if sort not in valid_sorts:
        sort = "chaos"

    page = max(1, page)
    offset = (page - 1) * PAGE_SIZE

    cards = await deps.db.list_cards(
        limit=PAGE_SIZE,
        offset=offset,
        sort=sort,
        container_id=container_id,
    )
    total = await deps.db.count_cards(container_id=container_id)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)

    for card in cards:
        card["_display_name"] = _display_name(card)
        card["_flag"] = LANG_FLAGS.get(card.get("language", "en"), "")

    container["_format_label"] = _format_label(container.get("deck_format"))
    is_deck = container.get("type") in DECK_TYPES

    return templates.TemplateResponse(request, "containers/detail.html", {
        "container": container,
        "cards": cards,
        "page": page,
        "total": total,
        "total_pages": total_pages,
        "sort": sort,
        "deck_formats": DECK_FORMATS,
        "is_deck": is_deck,
    })


@router.post("/containers/create")
async def containers_create(
    request: Request,
    name: str = Form(...),
    type: str = Form("binder"),
    deck_format: str = Form(""),
    _csrf_token: str = Form(...),
):
    verify_csrf(request, _csrf_token)
    if not name.strip():
        return RedirectResponse(url="/containers", status_code=303)
    # Sanitise type
    ctype = type if type in CONTAINER_TYPES else "binder"
    container_id = await deps.db.create_container(name.strip(), type=ctype)
    # Set format if this is a deck-type container
    fmt = deck_format.strip() if deck_format.strip() in _VALID_FORMATS else None
    if ctype in DECK_TYPES and fmt:
        await deps.db.set_container_deck_format(container_id, fmt)
    return RedirectResponse(url="/containers", status_code=303)


@router.post("/containers/{container_id}/rename")
async def containers_rename(
    request: Request,
    container_id: int,
    name: str = Form(...),
    _csrf_token: str = Form(...),
):
    verify_csrf(request, _csrf_token)
    if name.strip():
        await deps.db.rename_container(container_id, name.strip())
    return RedirectResponse(url=request.url_for("container_detail", container_id=container_id), status_code=303)


@router.post("/containers/{container_id}/set-format")
async def containers_set_format(
    request: Request,
    container_id: int,
    deck_format: str = Form(""),
    _csrf_token: str = Form(...),
):
    verify_csrf(request, _csrf_token)
    fmt = deck_format.strip() if deck_format.strip() in _VALID_FORMATS else None
    await deps.db.set_container_deck_format(container_id, fmt)
    return RedirectResponse(url=request.url_for("container_detail", container_id=container_id), status_code=303)


@router.post("/containers/{container_id}/delete")
async def containers_delete(
    request: Request,
    container_id: int,
    _csrf_token: str = Form(...),
):
    verify_csrf(request, _csrf_token)
    total = await deps.db.count_cards(container_id=container_id)
    if total == 0:
        await deps.db.delete_container(container_id)
    return RedirectResponse(url="/containers", status_code=303)
