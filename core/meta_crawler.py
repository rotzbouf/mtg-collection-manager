"""Competitive meta crawler — fetches deck data from mtgtop8.com.

mtgdecks.net is blocked by Cloudflare; mtgtop8.com serves plain HTML.

Format codes (mtgtop8):
  LE  → Legacy
  MO  → Modern
  ST  → Standard
  VI  → Vintage
  PAU → Pauper
  EDH → Commander
  PI  → Pioneer

Excluded (Arena-only): ALCH (Alchemy), HI (Historic)

Usage:
    from core.meta_crawler import crawl_formats
    total = await crawl_formats(db, ["LE", "MO"], max_events=10, progress_cb=cb)
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Callable, Optional

import aiohttp

logger = logging.getLogger(__name__)

_BASE = "https://www.mtgtop8.com"

# mtgtop8 code → human label (for display only)
FORMAT_LABELS: dict[str, str] = {
    "LE":  "Legacy",
    "MO":  "Modern",
    "ST":  "Standard",
    "VI":  "Vintage",
    "PAU": "Pauper",
    "EDH": "Commander",
    "PI":  "Pioneer",
}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64; rv:124.0) Gecko/20100101 Firefox/124.0"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=20)
# Be polite: 1 request/s sustained
_DELAY_BETWEEN_REQUESTS = 1.0


# ── HTML parsing helpers ────────────────────────────────────────────────── #

# mtgtop8.com uses raw & in URLs (not &amp;) inside HTML attributes
_EVENT_LINK_RE = re.compile(r'event\?e=(\d+)(?:&amp;|&)f=(\w+)')
_DECK_LINK_RE  = re.compile(r'\?e=(\d+)(?:&amp;|&)d=(\d+)(?:&amp;|&)f=(\w+)')
_PLACE_RE      = re.compile(r'<div[^>]+class=S14[^>]*>([^<]+)</div>')
_PLAYER_RE     = re.compile(r'<div[^>]+class=S14[^>]*>[^<]*<br\s*/?>\s*([^<]+)</div>', re.S)


def _parse_format_page(html: str, fmt_code: str) -> list[dict]:
    """Return list of {event_id, fmt_code, is_mtgo} dicts from a format page."""
    events: list[dict] = []
    seen: set[str] = set()

    for match in _EVENT_LINK_RE.finditer(html):
        eid  = match.group(1)
        fmtc = match.group(2)
        if eid in seen:
            continue
        seen.add(eid)
        # Determine MTGO vs paper from the current row (look backward to nearest <tr>)
        row_start = html.rfind("<tr", 0, match.start())
        if row_start < 0:
            row_start = max(0, match.start() - 600)
        snippet = html[row_start: match.end() + 100]
        is_mtgo = "online/mtgo.png" in snippet or (
            "mtgo.png" in snippet and "paper.png" not in snippet
        )
        events.append({"event_id": eid, "fmt_code": fmtc, "is_mtgo": is_mtgo})

    return events


def _parse_event_page(html: str) -> list[dict]:
    """Return list of {event_id, deck_id, fmt_code, place, player} from an event page."""
    decks: list[dict] = []
    seen: set[str] = set()

    # Find all deck links first
    for m in _DECK_LINK_RE.finditer(html):
        eid, did, fmtc = m.group(1), m.group(2), m.group(3)
        if did in seen:
            continue
        seen.add(did)

        # Look backwards for placement info (within 600 chars)
        start = max(0, m.start() - 600)
        snippet = html[start: m.end() + 200]

        place: Optional[str] = None
        pm = _PLACE_RE.search(snippet)
        if pm:
            place = pm.group(1).strip()

        player: Optional[str] = None
        # Player name often follows the place in the same cell
        lines = snippet.replace("\r", "").split("\n")
        for line in lines:
            line = line.strip()
            if line and not re.search(r'[<>]', line) and not line.startswith("&"):
                # Simple heuristic: first plain-text word-like thing
                candidate = re.sub(r'[^\w\s\-\.]', '', line).strip()
                if 2 < len(candidate) < 50:
                    player = candidate
                    break

        decks.append({
            "event_id": eid,
            "deck_id":  did,
            "fmt_code": fmtc,
            "place":    place,
            "player":   player,
        })

    return decks


def _parse_deck_page(html: str) -> list[dict]:
    """Return list of {name, quantity, section} from a deck page.

    mtgtop8 deck rows look like:
      <div id=mdNEO412 class="deck_line hover_tr" onclick="AffCard(...)">2 <span class=L14>Card Name</span> </div>
      <div id=sbNEO412 …>3 <span class=L14>Other Card</span> </div>

    IDs are md|sb + alphanumeric set code + collector number (e.g. mdneo412, sbdis172).
    """
    cards: list[dict] = []

    # Locate SIDEBOARD marker position for section classification
    sideboard_start = -1
    for marker in ("<div class=O14>SIDEBOARD</div>", "SIDEBOARD"):
        idx = html.find(marker)
        if idx >= 0:
            sideboard_start = idx
            break

    # Primary pattern: id=md<alphanum> or id=sb<alphanum> with deck_line class
    card_pattern = re.compile(
        r'<div\s+id=(md|sb)\w+\s+class="[^"]*deck_line[^"]*"[^>]*>'
        r'\s*(\d+)\s*<span\s+class=L14>([^<]+)</span>',
        re.S
    )

    for m in card_pattern.finditer(html):
        prefix = m.group(1)   # 'md' or 'sb'
        qty    = int(m.group(2))
        name   = m.group(3).strip()
        sec    = "side" if prefix == "sb" else "main"
        cards.append({"name": name, "quantity": qty, "section": sec})

    if not cards:
        # Fallback: any deck_line div with qty + L14, infer section from SIDEBOARD marker
        fallback = re.compile(
            r'class="[^"]*deck_line[^"]*"[^>]*>\s*(\d+)\s*<span\s+class=L14>([^<]+)</span>',
            re.S
        )
        for m in fallback.finditer(html):
            sec = "side" if sideboard_start >= 0 and m.start() > sideboard_start else "main"
            cards.append({"name": m.group(2).strip(), "quantity": int(m.group(1)), "section": sec})

    return cards


# ── Async fetch helpers ─────────────────────────────────────────────────── #

async def _fetch(session: aiohttp.ClientSession, url: str) -> str:
    """Fetch a URL and return the HTML text, or '' on error."""
    try:
        async with session.get(url, headers=_HEADERS, timeout=_REQUEST_TIMEOUT) as resp:
            if resp.status != 200:
                logger.warning("HTTP %d for %s", resp.status, url)
                return ""
            return await resp.text(encoding="utf-8", errors="replace")
    except Exception as exc:
        logger.warning("Fetch error for %s: %s", url, exc)
        return ""


# ── Public API ──────────────────────────────────────────────────────────── #

ProgressCb = Callable[[str, int, int], None]


async def crawl_format(
    db,
    fmt_code: str,
    *,
    max_events: int = 15,
    include_mtgo: bool = True,
    include_paper: bool = True,
    progress_cb: Optional[ProgressCb] = None,
) -> int:
    """Crawl one format from mtgtop8.com and store new decks in DB.

    Returns the number of new decks saved.
    """
    source = "mtgtop8"
    saved = 0
    already_stored = await db.get_crawled_deck_ids(source, fmt_code)

    def _report(msg: str, done: int = 0, total: int = 0):
        if progress_cb:
            progress_cb(msg, done, total)
        logger.debug("meta_crawler [%s]: %s", fmt_code, msg)

    async with aiohttp.ClientSession() as session:
        # ── 1. Format page → list of events ────────────────────────── #
        fmt_url = f"{_BASE}/format?f={fmt_code}"
        _report(f"Fetching format page for {FORMAT_LABELS.get(fmt_code, fmt_code)}…")
        html = await _fetch(session, fmt_url)
        if not html:
            _report("Failed to fetch format page.")
            return 0
        await asyncio.sleep(_DELAY_BETWEEN_REQUESTS)

        events = _parse_format_page(html, fmt_code)
        events = [e for e in events
                  if (include_mtgo and e["is_mtgo"]) or (include_paper and not e["is_mtgo"])]
        events = events[:max_events]

        if not events:
            _report("No events found.")
            return 0

        _report(f"Found {len(events)} event(s) to process.", 0, len(events))

        # ── 2. Each event page → list of decks ────────────────────── #
        all_decks: list[dict] = []
        for i, ev in enumerate(events):
            ev_url = f"{_BASE}/event?e={ev['event_id']}&f={ev['fmt_code']}"
            _report(f"Event {i+1}/{len(events)}: {ev_url}", i, len(events))
            ev_html = await _fetch(session, ev_url)
            await asyncio.sleep(_DELAY_BETWEEN_REQUESTS)
            if not ev_html:
                continue
            decks = _parse_event_page(ev_html)
            for d in decks:
                d["is_mtgo"] = ev["is_mtgo"]
            all_decks.extend(decks)

        # Filter already-stored decks
        new_decks = [d for d in all_decks if d["deck_id"] not in already_stored]
        _report(f"Fetching {len(new_decks)} new deck(s) (skipping {len(all_decks)-len(new_decks)} known)…",
                0, len(new_decks))

        # ── 3. Each deck page → cards ──────────────────────────────── #
        for i, deck in enumerate(new_decks):
            deck_url = (
                f"{_BASE}/event?e={deck['event_id']}&d={deck['deck_id']}&f={deck['fmt_code']}"
            )
            _report(f"Deck {i+1}/{len(new_decks)}: #{deck['deck_id']} (place: {deck.get('place', '?')})",
                    i, len(new_decks))
            dk_html = await _fetch(session, deck_url)
            await asyncio.sleep(_DELAY_BETWEEN_REQUESTS)
            if not dk_html:
                continue

            cards = _parse_deck_page(dk_html)
            if not cards:
                logger.debug("No cards parsed for deck %s — skipping", deck["deck_id"])
                continue

            ok = await db.save_meta_deck(
                source=source,
                format_code=fmt_code,
                event_id=deck["event_id"],
                deck_id=deck["deck_id"],
                player=deck.get("player"),
                place=deck.get("place"),
                cards=cards,
            )
            if ok:
                saved += 1

    _report(f"Done — {saved} new deck(s) saved.", saved, len(new_decks))
    return saved


async def crawl_formats(
    db,
    fmt_codes: list[str],
    *,
    max_events: int = 15,
    include_mtgo: bool = True,
    include_paper: bool = True,
    progress_cb: Optional[ProgressCb] = None,
) -> int:
    """Crawl multiple formats sequentially.  Returns total new decks saved."""
    total = 0
    for fmt_code in fmt_codes:
        n = await crawl_format(
            db, fmt_code,
            max_events=max_events,
            include_mtgo=include_mtgo,
            include_paper=include_paper,
            progress_cb=progress_cb,
        )
        total += n
    return total
