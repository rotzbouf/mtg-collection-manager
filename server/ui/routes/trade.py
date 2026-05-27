"""Trade / Sell assistant — match a store buylist against the collection."""
from __future__ import annotations

import asyncio
import ipaddress
import os
import logging
import socket
from typing import Optional
from urllib.parse import urlparse, urljoin

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

import server.ui.deps as deps
from server.ui.csrf import verify_csrf
from core.buylist_parser import parse_buylist_text, is_cardkingdom_url, CARDKINGDOM_API_URL
from core.fx import get_usd_eur_rate

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
templates = Jinja2Templates(directory=_TEMPLATES_DIR)

router = APIRouter()

async def _check_ssrf(url: str) -> Optional[str]:
    """Return an error string if *url* should be blocked, else None.

    Defends against SSRF by:
    - Allowing only http / https schemes.
    - Resolving the hostname via getaddrinfo (IPv4 *and* IPv6) and rejecting
      private, loopback, link-local, reserved, multicast and unspecified
      addresses (e.g. 127.x, 10.x, 172.16-31.x, 192.168.x, 169.254.x,
      ::1, cloud metadata endpoints).

    DNS resolution is performed in a thread executor to avoid blocking the
    event loop.  Note: this check is a best-effort guard — it does not prevent
    DNS-rebinding attacks, but it eliminates the straightforward SSRF vector.
    Call this function for *every* URL that will be fetched, including
    redirect destinations.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return "Malformed URL."

    if parsed.scheme not in ("http", "https"):
        return f"Only http:// and https:// URLs are allowed (got '{parsed.scheme}://')."

    host = parsed.hostname
    if not host:
        return "URL contains no hostname."

    try:
        loop = asyncio.get_running_loop()
        # getaddrinfo covers both IPv4 and IPv6 (gethostbyname only does IPv4).
        infos = await loop.run_in_executor(
            None,
            lambda: socket.getaddrinfo(host, None, socket.AF_UNSPEC, socket.SOCK_STREAM),
        )
    except OSError:
        return f"Cannot resolve hostname: {host}"

    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return f"Could not parse resolved address for {host}."
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return "Requests to private or internal addresses are not allowed."

    return None


_FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
}


def _resolve_key(
    card: dict,
    buylist_by_name: dict[str, dict],
    buylist_by_sid: dict[str, dict] | None = None,
) -> "tuple[str, dict] | None":
    """Return ``(group_key, bl_entry)`` for the best match, or ``None``.

    Priority: scryfall_id → name + set code → name only.
    """
    sid = card.get("scryfall_id")
    if sid and buylist_by_sid:
        entry = buylist_by_sid.get(sid)
        if entry is not None:
            return entry["name"].lower(), entry

    for field in ("name_en", "name_de", "printed_name"):
        v = (card.get(field) or "").lower()
        if not v or v not in buylist_by_name:
            continue
        entry    = buylist_by_name[v]
        bl_set   = (entry.get("set") or "").upper().strip()
        card_set = (card.get("set_code") or "").upper().strip()
        if bl_set and card_set and bl_set != card_set:
            continue
        return v, entry

    return None


async def _fetch_url(url: str) -> tuple[Optional[str], Optional[str]]:
    """Fetch a URL and return (text, error_message).

    Card Kingdom URLs are redirected to their public JSON API automatically.
    All URLs are validated against an SSRF guard before any network request.
    """
    import aiohttp, json as _json

    ssrf_err = await _check_ssrf(url)
    if ssrf_err:
        return None, ssrf_err

    try:
        # Card Kingdom: use public JSON API directly
        if is_cardkingdom_url(url):
            async with aiohttp.ClientSession(
                headers={**_FETCH_HEADERS, "Accept": "application/json"},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as session:
                async with session.get(CARDKINGDOM_API_URL) as resp:
                    if resp.status != 200:
                        return None, f"Card Kingdom API: HTTP {resp.status}"
                    data = await resp.json(content_type=None)
            return _json.dumps(data, separators=(",", ":")), None

        jar = aiohttp.CookieJar(unsafe=True)
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(
            headers=_FETCH_HEADERS,
            cookie_jar=jar,
            timeout=timeout,
        ) as session:
            # Follow redirects manually so every Location header is validated
            # through _check_ssrf before we connect — prevents redirect-based
            # SSRF bypasses (e.g. open-redirect on the initial host pointing
            # back to 169.254.x or 192.168.x).
            _MAX_REDIRECTS = 10
            current_url = url
            html = ""
            for _ in range(_MAX_REDIRECTS):
                async with session.get(current_url, allow_redirects=False) as resp:
                    if resp.status in (301, 302, 303, 307, 308):
                        location = resp.headers.get("Location", "")
                        if not location:
                            return None, "Redirect with no Location header."
                        next_url = urljoin(current_url, location)
                        hop_err = await _check_ssrf(next_url)
                        if hop_err:
                            return None, f"Redirect blocked: {hop_err}"
                        current_url = next_url
                        continue
                    html = await resp.text(errors="replace")
                    break
            else:
                return None, "Too many redirects."
        if len(html) < 300:
            return None, "Response too short — the site may require JavaScript."
        return html, None
    except Exception as exc:
        return None, str(exc)


async def _convert_entries(entries: list[dict]) -> Optional[float]:
    """Convert USD-priced entries to EUR in-place.  Returns rate used or None."""
    usd = [e for e in entries if e.get("currency") == "USD"]
    if not usd:
        return None
    rate = await get_usd_eur_rate()
    for e in usd:
        if e.get("price") is not None:
            e["price"]    = round(e["price"] * rate, 4)
            e["currency"] = "EUR"
    return rate


async def _match_buylist(entries: list[dict]) -> list[dict]:
    """Match parsed buylist entries against the collection.

    Returns a list of match dicts with keys:
      name, set_code, bl_price, mkt_price, count, container, above_market
    """
    if not entries:
        return []

    buylist_by_name: dict[str, dict] = {}
    buylist_by_sid:  dict[str, dict] = {}
    for e in entries:
        buylist_by_name[e["name"].lower()] = e
        if e.get("scryfall_id"):
            buylist_by_sid[e["scryfall_id"]] = e

    collection_cards = await deps.db.get_cards_by_names(
        list(buylist_by_name.keys()),
        exclude_container_types=["deck", "commander"],
    )

    grouped: dict[str, dict] = {}
    for card in collection_cards:
        result = _resolve_key(card, buylist_by_name, buylist_by_sid)
        if result is None:
            continue
        key, bl_entry = result
        if key not in grouped:
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
        "fx_rate": None,
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
            "fx_rate": None,
            "has_brave": bool(brave_key),
        })

    fx_rate = await _convert_entries(entries)
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
        "fx_rate": fx_rate,
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
                "fx_rate": None,
            })
            continue

        fx_rate = await _convert_entries(entries)
        matches = await _match_buylist(entries)
        total_bl  = sum((m["bl_price"]  or 0.0) * m["count"] for m in matches)
        total_mkt = sum((m["mkt_price"] or 0.0) * m["count"] for m in matches)
        above     = sum(1 for m in matches if m.get("above_market"))

        store_results.append({
            "title": title, "url": url, "status": "ok",
            "matches": matches,
            "total_bl": total_bl, "total_mkt": total_mkt, "above_market": above,
            "fx_rate": fx_rate,
        })

    # Sort by total buylist value descending
    store_results.sort(key=lambda s: s["total_bl"], reverse=True)

    return templates.TemplateResponse(request, "trade/search_results.html", {
        "store_results": store_results,
        "error": None,
        "query": query,
    })
