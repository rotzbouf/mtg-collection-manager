"""Container routes."""
from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import ui.deps as deps

_TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
templates = Jinja2Templates(directory=_TEMPLATES_DIR)

router = APIRouter()

PAGE_SIZE = 25

CONTAINER_TYPES = ["binder", "box", "deck", "trade", "other"]

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


@router.get("/containers", response_class=HTMLResponse)
async def containers_list(request: Request):
    containers = await deps.db.list_containers()
    return templates.TemplateResponse("containers/list.html", {
        "request": request,
        "containers": containers,
        "container_types": CONTAINER_TYPES,
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

    return templates.TemplateResponse("containers/detail.html", {
        "request": request,
        "container": container,
        "cards": cards,
        "page": page,
        "total": total,
        "total_pages": total_pages,
        "sort": sort,
    })


@router.post("/containers/create")
async def containers_create(
    name: str = Form(...),
    type: str = Form("binder"),
):
    if not name.strip():
        return RedirectResponse(url="/containers", status_code=303)
    await deps.db.create_container(name.strip(), type=type)
    return RedirectResponse(url="/containers", status_code=303)


@router.post("/containers/{container_id}/rename")
async def containers_rename(
    container_id: int,
    name: str = Form(...),
):
    if name.strip():
        await deps.db.rename_container(container_id, name.strip())
    return RedirectResponse(url=f"/containers/{container_id}", status_code=303)


@router.post("/containers/{container_id}/delete")
async def containers_delete(container_id: int):
    # Only delete if empty
    total = await deps.db.count_cards(container_id=container_id)
    if total == 0:
        await deps.db.delete_container(container_id)
    return RedirectResponse(url="/containers", status_code=303)
