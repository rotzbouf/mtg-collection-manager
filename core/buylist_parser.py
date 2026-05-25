"""Pure-Python buylist text parsing — no Qt dependencies.

Shared between the desktop Buylists widget and the web UI trade assistant.

Supported input formats
-----------------------
- Card Kingdom JSON  — raw response from https://api.cardkingdom.com/api/pricelist
- HTML               — ``<table>`` elements from a fetched page
- TSV                — browser table copy (name \\t price, or name \\t set \\t price)
- CSV                — comma-separated equivalent
- Plain text         — one card name per line, optionally with a trailing price
"""
from __future__ import annotations

import json as _json
import re
from html.parser import HTMLParser
from typing import Optional


# ── HTML table parser ──────────────────────────────────────────────────────────

class _TableParser(HTMLParser):
    """Extract all <table> contents as list[list[str]] rows."""

    def __init__(self):
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._in_table = False
        self._cur_row: list[str] = []
        self._cur_cell: list[str] = []
        self._in_cell = False

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._in_table = True
            self.tables.append([])
        elif tag in ("tr",) and self._in_table:
            self._cur_row = []
        elif tag in ("td", "th") and self._in_table:
            self._in_cell = True
            self._cur_cell = []

    def handle_endtag(self, tag):
        if tag == "table":
            self._in_table = False
        elif tag == "tr" and self._in_table and self._cur_row:
            self.tables[-1].append(self._cur_row)
            self._cur_row = []
        elif tag in ("td", "th") and self._in_table:
            self._in_cell = False
            self._cur_row.append(" ".join(self._cur_cell).strip())
            self._cur_cell = []

    def handle_data(self, data):
        if self._in_cell:
            self._cur_cell.append(data.strip())


# ── Price / header helpers ─────────────────────────────────────────────────────

_PRICE_RE = re.compile(r"(\d+[.,]\d+)")


def _parse_price(text: str) -> Optional[float]:
    m = _PRICE_RE.search(text)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", "."))
    except ValueError:
        return None


def _is_header_row(row: list[str]) -> bool:
    """Heuristic: row is a header if the first cell is a known column label."""
    if not row:
        return False
    first = row[0].lower()
    return any(kw in first for kw in ("name", "karte", "card", "edition", "set", "preis", "price"))


# ── Card Kingdom JSON API ──────────────────────────────────────────────────────

#: Public Card Kingdom pricelist endpoint — no auth required.
CARDKINGDOM_API_URL = "https://api.cardkingdom.com/api/pricelist"

#: Hostnames recognised as Card Kingdom.
CARDKINGDOM_HOSTS: frozenset[str] = frozenset({
    "cardkingdom.com",
    "www.cardkingdom.com",
})


def is_cardkingdom_url(url: str) -> bool:
    """Return True if *url* points to a Card Kingdom domain."""
    try:
        from urllib.parse import urlparse
        host = urlparse(url).hostname or ""
        return host.lower().lstrip("www.") in {h.lstrip("www.") for h in CARDKINGDOM_HOSTS}
    except Exception:
        return False


def parse_cardkingdom_api(data: dict) -> list[dict]:
    """Parse a Card Kingdom pricelist API response dict.

    Only cards with ``qty_buying > 0`` are included (i.e. those the store
    is actively buying).  Prices are in **USD**.

    Each returned dict has keys:
    ``name``, ``price`` (float | None, USD), ``set`` (str | None, set code),
    ``scryfall_id`` (str | None), ``foil`` (bool), ``qty_buying`` (int).
    """
    entries: list[dict] = []
    for item in data.get("data", []):
        try:
            qty_buying = int(item.get("qty_buying") or 0)
        except (TypeError, ValueError):
            qty_buying = 0
        if qty_buying == 0:
            continue

        name = (item.get("name") or "").strip()
        if not name:
            continue

        try:
            price: Optional[float] = float(item.get("price_buy") or 0) or None
        except (TypeError, ValueError):
            price = None

        # SKU format: "4ED-117" → set code "4ED"
        sku = item.get("sku") or ""
        set_code: Optional[str] = sku.split("-")[0] if "-" in sku else None

        foil = str(item.get("is_foil", "false")).lower() == "true"

        entries.append({
            "name":        name,
            "price":       price,
            "currency":    "USD",   # Card Kingdom pays in USD
            "set":         set_code,
            "scryfall_id": item.get("scryfall_id") or None,
            "foil":        foil,
            "qty_buying":  qty_buying,
        })
    return entries


# ── Public API ─────────────────────────────────────────────────────────────────

def parse_buylist_text(text: str) -> list[dict]:
    """Parse pasted or fetched buylist text into a list of dicts.

    Each dict has keys: ``name`` (str), ``price`` (float | None), ``set`` (str | None).

    Handles:
    - HTML from URL fetch (``<table>`` elements)
    - Tab-separated browser table copies (name \\t price; or name \\t set \\t price)
    - Comma-separated values
    - Plain one-card-per-line lists
    """
    text = text.strip()
    if not text:
        return []

    # Card Kingdom JSON API response  ("{...\"price_buy\"...")
    if text.startswith("{") and '"price_buy"' in text:
        try:
            data = _json.loads(text)
            if isinstance(data.get("data"), list):
                return parse_cardkingdom_api(data)
        except Exception:
            pass

    # HTML path
    if text.lstrip().startswith("<"):
        return _parse_html_buylist(text)

    # Tab / comma separated
    lines = [ln for ln in text.splitlines() if ln.strip()]
    entries: list[dict] = []
    for line in lines:
        parts = [p.strip() for p in (line.split("\t") if "\t" in line else line.split(","))]
        if not parts or not parts[0]:
            continue
        if _is_header_row(parts):
            continue

        name = parts[0]
        price: Optional[float] = None
        set_code: Optional[str] = None

        # Price: last column that looks like a decimal number
        for p in reversed(parts[1:]):
            price = _parse_price(p)
            if price is not None:
                break

        # Set: second column when there are ≥3 columns and it is not a price
        if len(parts) >= 3:
            set_code = parts[1] if not _parse_price(parts[1]) else None

        entries.append({"name": name, "price": price, "set": set_code})

    return entries


def _parse_html_buylist(html: str) -> list[dict]:
    parser = _TableParser()
    parser.feed(html)

    entries: list[dict] = []
    for table in parser.tables:
        for row in table:
            if not row or not row[0]:
                continue
            if _is_header_row(row):
                continue

            name = row[0]
            price: Optional[float] = None
            set_code: Optional[str] = None

            for cell in reversed(row[1:]):
                price = _parse_price(cell)
                if price is not None:
                    break
            if len(row) >= 3:
                set_code = row[1] if not _parse_price(row[1]) else None

            entries.append({"name": name, "price": price, "set": set_code})

    return entries
