"""Trade / Sell assistant — match a store buylist against the collection."""
from __future__ import annotations

import os
import logging
from typing import Optional

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

import server.ui.deps as deps
from server.ui.csrf import verify_csrf
from core.buylist_parser import parse_buylist_text

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
templates = Jinja2Templates(directory=_TEMPLATES_DIR)

router = APIRouter()

_FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
}


def _resolve_key(card: dict, buylist: dict[str, dict]) -> Optional[str]:
    """Try matching a collection card to a buylist entry by any name field."""
    for field in ("name_en", "name_de", "printed_name"):
        v = (card.get(field) or "").lower()
        if v and v in buylist:
            return v
    return None


async def _fetch_url(url: str) -> tuple[Optional[str], Optional[str]]:
    """Fetch a URL and return (html, error_message)."""
    import aiohttp
    try:
        jar = aiohttp.CookieJar(unsafe=True)
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(
            headers=_FETCH_HEADERS,
            cookie_jar=jar,
            timeout=timeout,
        ) as session:
            async with session.get(url, allow_redirects=True) as resp:
                html = await resp.text(errors="replace")
        if len(html) < 300:
            return None, "Response too short — the site may require JavaScript."
        return html, None
    except Exception as exc:
        return None, str(exc)


async def _match_buylist(entries: list[dict]) -> list[dict]:
    """Match parsed buylist entries against the collection.

    Returns a list of match dicts with keys:
      name, set_code, bl_price, mkt_price, count, container, above_market
    """
    if not entries:
        return []

    buylist_by_name: dict[str, dict] = {}
    for e in entries:
        buylist_by_name[e["name"].lower()] = e

    collection_cards = await deps.db.get_cards_by_names(
        list(buylist_by_name.keys()),
        exclude_container_types=["deck", "commander"],
    )

    grouped: dict[str, dict] = {}
    for card in collection_cards:
        key = _resolve_key(card, buylist_by_name)
        if key is None:
            continue
        if key not in grouped:
            bl_entry = buylist_by_name[key]
            grouped[key] = {
                "name":      card.get("name_en") or card.get("printed_name") or key,
                "set_code":  bl_entry.get("set") or card.get("set_code") or "",
                "bl_price":  bl_entry.get("price"),
                "mkt_price": card.get("price_eur"),
                "count":     0,
                "container": card.get("container_name") or "—",
            }
        grouped[key]["count"] += 1
        mkt = card.get("price_eur") or 0.0
        if mkt > (grouped[key]["mkt_price"] or 0.0):
            grouped[key]["mkt_price"] = mkt
        ct = card.get("container_name") or "—"
        existing = grouped[key]["container"]
        if ct not in existing:
            grouped[key]["container"] = f"{existing}, {ct}" if existing != "—" else ct

    matches = list(grouped.values())
    # Tag above-market (buylist >= 80 % of market price)
    for m in matches:
        bl = m["bl_price"] or 0.0
        mkt = m["mkt_price"] or 0.0
        m["above_market"] = mkt > 0 and bl >= mkt * 0.8
    # Sort by buylist price descending
    matches.sort(key=lambda x: x["bl_price"] or 0.0, reverse=True)
    return matches


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/trade", response_class=HTMLResponse)
async def trade_page(request: Request):
    import core.config as _cfg
    brave_key = _cfg.load().get("brave", {}).get("api_key", "")
    return templates.TemplateResponse(request, "trade/index.html", {
        "matches": None,
        "error": None,
        "url": "",
        "text": "",
        "entry_count": 0,
        "total_bl": 0.0,
        "total_mkt": 0.0,
        "has_brave": bool(brave_key),
    })


@router.post("/trade/match", response_class=HTMLResponse)
async def trade_match(
    request: Request,
    csrf_token: str = Form(""),
    buylist_url: str = Form(""),
    buylist_text: str = Form(""),
):
    verify_csrf(request, csrf_token)

    error: Optional[str] = None
    raw_text = buylist_text.strip()
    fetched_url = buylist_url.strip()

    # Fetch from URL if provided and no pasted text
    if fetched_url and not raw_text:
        html, err = await _fetch_url(fetched_url)
        if err:
            error = f"Could not fetch URL: {err}. Try pasting the buylist text directly."
        else:
            raw_text = html or ""

    import core.config as _cfg
    brave_key = _cfg.load().get("brave", {}).get("api_key", "")

    if error:
        return templates.TemplateResponse(request, "trade/index.html", {
            "matches": None,
            "error": error,
            "url": fetched_url,
            "text": buylist_text,
            "entry_count": 0,
            "total_bl": 0.0,
            "total_mkt": 0.0,
            "has_brave": bool(brave_key),
        })

    entries = parse_buylist_text(raw_text)
    if not entries:
        return templates.TemplateResponse(request, "trade/index.html", {
            "matches": None,
            "error": "No buylist entries found. Check the pasted format.",
            "url": fetched_url,
            "text": buylist_text,
            "entry_count": 0,
            "total_bl": 0.0,
            "total_mkt": 0.0,
            "has_brave": bool(brave_key),
        })

    matches = await _match_buylist(entries)
    total_bl  = sum((m["bl_price"]  or 0.0) * m["count"] for m in matches)
    total_mkt = sum((m["mkt_price"] or 0.0) * m["count"] for m in matches)

    return templates.TemplateResponse(request, "trade/index.html", {
        "matches": matches,
        "error": None,
        "url": fetched_url,
        "text": buylist_text,
        "entry_count": len(entries),
        "total_bl": total_bl,
        "total_mkt": total_mkt,
        "has_brave": bool(brave_key),
    })


@router.post("/trade/search-urls", response_class=HTMLResponse)
async def trade_search_urls(
    request: Request,
    csrf_token: str = Form(""),
    query: str = Form(""),
    max_results: int = Form(5),
):
    """Use Brave Search to discover buylist URLs, fetch and match the best ones."""
    verify_csrf(request, csrf_token)

    import core.config as _cfg
    from core.brave_search import search_buylist_urls
    import aiohttp

    brave = _cfg.load().get("brave", {})
    api_key = brave.get("api_key", "").strip()

    if not api_key:
        return templates.TemplateResponse(request, "trade/search_results.html", {
            "store_results": [],
            "error": "No Brave API key configured. Set brave.api_key in config.json.",
            "query": query,
        })

    query = query.strip()
    if not query:
        return templates.TemplateResponse(request, "trade/search_results.html", {
            "store_results": [],
            "error": "Please enter a search query.",
            "query": query,
        })

    max_results = max(1, min(20, max_results))

    try:
        urls = await search_buylist_urls(api_key, query, max_results)
    except Exception as exc:
        return templates.TemplateResponse(request, "trade/search_results.html", {
            "store_results": [],
            "error": f"Search failed: {exc}",
            "query": query,
        })

    store_results = []
    for hit in urls:
        url   = hit["url"]
        title = hit["title"] or url

        html, _err = await _fetch_url(url)
        if html is None:
            store_results.append({
                "title": title, "url": url, "status": "fetch_error",
                "matches": [], "total_bl": 0.0, "total_mkt": 0.0, "above_market": 0,
            })
            continue

        entries = parse_buylist_text(html)
        if not entries:
            store_results.append({
                "title": title, "url": url, "status": "no_entries",
                "matches": [], "total_bl": 0.0, "total_mkt": 0.0, "above_market": 0,
            })
            continue

        matches = await _match_buylist(entries)
        total_bl  = sum((m["bl_price"]  or 0.0) * m["count"] for m in matches)
        total_mkt = sum((m["mkt_price"] or 0.0) * m["count"] for m in matches)
        above     = sum(1 for m in matches if m.get("above_market"))

        store_results.append({
            "title": title, "url": url, "status": "ok",
            "matches": matches,
            "total_bl": total_bl, "total_mkt": total_mkt, "above_market": above,
        })

    # Sort by total buylist value descending
    store_results.sort(key=lambda s: s["total_bl"], reverse=True)

    return templates.TemplateResponse(request, "trade/search_results.html", {
        "store_results": store_results,
        "error": None,
        "query": query,
    })
