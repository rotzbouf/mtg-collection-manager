"""Collection routes: list, detail, add, edit, delete."""
from __future__ import annotations

import json
import os
from typing import Optional

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import server.ui.deps as deps

_TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
templates = Jinja2Templates(directory=_TEMPLATES_DIR)

router = APIRouter()

PAGE_SIZE = 25

LANG_FLAGS = {
    "en": "🇬🇧", "de": "🇩🇪", "fr": "🇫🇷", "it": "🇮🇹",
    "ja": "🇯🇵", "ko": "🇰🇷", "ru": "🇷🇺", "pt": "🇵🇹",
    "zh": "🇨🇳", "zhs": "🇨🇳", "zht": "🇨🇳", "es": "🇪🇸",
}

SORT_OPTIONS = [
    ("chaos", "Chaos (default)"),
    ("name", "Name"),
    ("set", "Set"),
    ("cmc", "CMC"),
    ("added", "Recently Added"),
]

CONDITIONS = ["NM", "LP", "MP", "HP", "DMG"]
LANGUAGES = list(LANG_FLAGS.keys())


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


def _render(request: Request, template: str, ctx: dict):
    return templates.TemplateResponse(request, template, ctx)


@router.get("/collection", response_class=HTMLResponse)
async def collection_list(
    request: Request,
    q: str = "",
    container_id: Optional[int] = None,
    language: str = "",
    sort: str = "chaos",
    page: int = 1,
):
    page = max(1, page)
    offset = (page - 1) * PAGE_SIZE

    if q:
        cards = await deps.db.search(q, limit=PAGE_SIZE, offset=offset)
        total = await deps.db.count_search(q)
    else:
        lang_filter = language if language else None
        cards = await deps.db.list_cards(
            limit=PAGE_SIZE,
            offset=offset,
            sort=sort if sort in dict(SORT_OPTIONS) else "chaos",
            language=lang_filter,
            container_id=container_id,
        )
        total = await deps.db.count_cards(language=lang_filter, container_id=container_id)

    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    containers_list = await deps.db.list_containers()

    for card in cards:
        card["_display_name"] = _display_name(card)
        card["_flag"] = LANG_FLAGS.get(card.get("language", "en"), "")

    ctx = {
        "cards": cards,
        "q": q,
        "container_id": container_id,
        "language": language,
        "sort": sort,
        "page": page,
        "total": total,
        "total_pages": total_pages,
        "containers": containers_list,
        "sort_options": SORT_OPTIONS,
        "lang_flags": LANG_FLAGS,
        "page_size": PAGE_SIZE,
    }

    is_htmx = request.headers.get("HX-Request") == "true"
    template = "collection/_rows.html" if is_htmx else "collection/list.html"
    return _render(request, template, ctx)


@router.get("/collection/add", response_class=HTMLResponse)
async def collection_add_form(request: Request, name: str = "", set_code: str = ""):
    containers_list = await deps.db.list_containers()
    return _render(request, "collection/add.html", {
        "name": name,
        "set_code": set_code,
        "containers": containers_list,
        "conditions": CONDITIONS,
        "languages": LANGUAGES,
        "lang_flags": LANG_FLAGS,
        "card": None,
        "error": None,
    })


@router.post("/collection/add", response_class=HTMLResponse)
async def collection_add_submit(
    request: Request,
    name: str = Form(""),
    set_code: str = Form(""),
    action: str = Form("lookup"),
    scryfall_id: str = Form(""),
    name_en: str = Form(""),
    name_de: str = Form(""),
    printed_name: str = Form(""),
    set_code_hidden: str = Form(""),
    set_name: str = Form(""),
    collector_number: str = Form(""),
    rarity: str = Form(""),
    mana_cost: str = Form(""),
    cmc: str = Form("0"),
    type_line: str = Form(""),
    oracle_text: str = Form(""),
    image_url: str = Form(""),
    price_eur: str = Form(""),
    price_usd: str = Form(""),
    language: str = Form("en"),
    condition: str = Form("NM"),
    foil: str = Form(""),
    container_id: str = Form(""),
    oracle_id: str = Form(""),
    colors: str = Form("[]"),
    color_identity: str = Form("[]"),
    keywords: str = Form("[]"),
    legalities: str = Form("{}"),
):
    containers_list = await deps.db.list_containers()

    def _add_ctx(card=None, error=None):
        return _render(request, "collection/add.html", {
            "name": name,
            "set_code": set_code,
            "containers": containers_list,
            "conditions": CONDITIONS,
            "languages": LANGUAGES,
            "lang_flags": LANG_FLAGS,
            "card": card,
            "error": error,
        })

    if action == "lookup":
        if not name.strip():
            return _add_ctx(error="Please enter a card name.")
        sc = set_code.strip() or None
        card_data = await deps.scryfall.get_by_name(name.strip(), fuzzy=True, set_code=sc)
        if not card_data:
            card_data = await deps.scryfall.get_german(name.strip(), set_code=sc)
        if not card_data:
            return _add_ctx(error=f"Card not found on Scryfall: {name!r}")
        return _add_ctx(card=card_data)

    # action == "save"
    def _f(v: str) -> Optional[float]:
        try:
            return float(v) if v.strip() else None
        except ValueError:
            return None

    def _j(v: str, default):
        try:
            return json.loads(v) if v.strip() else default
        except Exception:
            return default

    card_to_save = {
        "scryfall_id": scryfall_id or None,
        "oracle_id": oracle_id or None,
        "name_en": name_en or name,
        "name_de": name_de or None,
        "printed_name": printed_name or None,
        "set_code": set_code_hidden or set_code or None,
        "set_name": set_name or None,
        "collector_number": collector_number or None,
        "rarity": rarity or None,
        "mana_cost": mana_cost or None,
        "cmc": float(cmc) if cmc else 0.0,
        "type_line": type_line or None,
        "oracle_text": oracle_text or None,
        "image_url": image_url or None,
        "price_eur": _f(price_eur),
        "price_usd": _f(price_usd),
        "language": language or "en",
        "condition": condition or "NM",
        "foil": 1 if foil else 0,
        "quantity": 1,
        "container_id": int(container_id) if container_id else None,
        "colors": _j(colors, []),
        "color_identity": _j(color_identity, []),
        "keywords": _j(keywords, []),
        "legalities": _j(legalities, {}),
    }
    new_id = await deps.db.add_card(card_to_save, added_by="web-ui")
    return RedirectResponse(url=f"/collection/{new_id}", status_code=303)


@router.get("/collection/{card_id}", response_class=HTMLResponse)
async def collection_detail(request: Request, card_id: int):
    card = await deps.db.get_card(card_id)
    if not card:
        return HTMLResponse("<h2>Card not found</h2>", status_code=404)
    card["_display_name"] = _display_name(card)
    card["_flag"] = LANG_FLAGS.get(card.get("language", "en"), "")
    containers_list = await deps.db.list_containers()
    return _render(request, "collection/detail.html", {
        "card": card,
        "containers": containers_list,
        "conditions": CONDITIONS,
        "languages": LANGUAGES,
        "lang_flags": LANG_FLAGS,
    })


@router.post("/collection/{card_id}/edit", response_class=HTMLResponse)
async def collection_edit(
    request: Request,
    card_id: int,
    condition: str = Form("NM"),
    foil: str = Form(""),
    language: str = Form("en"),
    container_id: str = Form(""),
    notes: str = Form(""),
):
    updatable = {
        "condition": condition,
        "foil": 1 if foil else 0,
        "language": language,
        "container_id": int(container_id) if container_id else None,
        "notes": notes or None,
    }
    for field, value in updatable.items():
        await deps.db.update_card(card_id, field, value)
    return RedirectResponse(url=f"/collection/{card_id}", status_code=303)


@router.post("/collection/{card_id}/delete")
async def collection_delete(card_id: int):
    await deps.db.remove_card(card_id)
    return RedirectResponse(url="/collection", status_code=303)
