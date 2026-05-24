"""
Scryfall API client.
Rate limit: max 10 req/s — we enforce a 100 ms gap between requests.
"""

import asyncio
import logging
import re
import time
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

BASE = "https://api.scryfall.com"
_HEADERS = {"Accept": "application/json;q=0.9,*/*;q=0.8"}
_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)
_SET_CODE_RE = re.compile(r"^[a-zA-Z0-9]{1,10}$")

# German MTG set codes that are purely German editions (very old)
_DE_SETS = {"por", "ptk", "s99"}
_CARD_DATA_TTL = 30 * 86_400.0  # 30 days — oracle text / type / image rarely change
_PRICE_TTL     =      86_400.0  # 24 h   — EUR/USD prices refresh daily


def _extract_card(data: dict, preferred_lang: Optional[str] = None) -> dict:
    """Normalize a Scryfall card object into our DB schema."""
    lang = data.get("lang", "en")

    # Prices
    prices = data.get("prices", {})

    # Image — for DFCs the top-level image_uris is absent; use the front face.
    images = data.get("image_uris", {})
    faces = data.get("card_faces")
    if not images and faces:
        images = faces[0].get("image_uris", {})
    image_url = images.get("normal") or images.get("small")

    # Back-face image URL (transform / modal DFCs only)
    image_url_back: Optional[str] = None
    if faces and len(faces) > 1:
        back_imgs = faces[1].get("image_uris", {})
        image_url_back = back_imgs.get("normal") or back_imgs.get("small")

    # ── Names ──────────────────────────────────────────────────────────────
    # For DFCs Scryfall puts printed_name (and mana_cost/power/toughness)
    # on individual card_faces, not the top-level object.
    top_printed = data.get("printed_name")

    if lang == "en":
        name_en = data.get("name", "")
        name_de = None
        printed = top_printed  # always None for EN cards
    else:
        name_en = data.get("name", "")  # Scryfall always gives the oracle (EN) name
        # Reconstruct the localized DFC name from faces when missing at top level
        if top_printed is None and faces:
            f0_p = faces[0].get("printed_name")
            f1_p = faces[1].get("printed_name") if len(faces) > 1 else None
            if f0_p:
                top_printed = f"{f0_p} // {f1_p}" if f1_p else f0_p
        printed = top_printed
        name_de = printed if lang == "de" else None

    # ── mana_cost / power / toughness ──────────────────────────────────────
    # On DFCs these live on card_faces[0], not the top-level object.
    mana_cost  = data.get("mana_cost")  or (faces[0].get("mana_cost")  if faces else None)
    power      = data.get("power")      or (faces[0].get("power")      if faces else None)
    toughness  = data.get("toughness")  or (faces[0].get("toughness")  if faces else None)

    # ── Oracle / type text ─────────────────────────────────────────────────
    if lang != "en":
        type_line = data.get("printed_type_line") or data.get("type_line", "")
        oracle_text = (
            data.get("printed_text")
            or (faces[0].get("printed_text") if faces else None)
            or data.get("oracle_text")
            or (faces[0].get("oracle_text") if faces else None)
        )
    else:
        type_line = data.get("type_line", "")
        oracle_text = data.get("oracle_text") or (faces[0].get("oracle_text") if faces else None)

    return {
        "scryfall_id":   data.get("id"),
        "oracle_id":     data.get("oracle_id"),
        "cardmarket_id": data.get("cardmarket_id"),
        "name_en":       name_en,
        "name_de":       name_de,
        "printed_name":  printed,
        "set_code":      data.get("set"),
        "set_name":      data.get("set_name"),
        "collector_number": data.get("collector_number"),
        "released_at":   data.get("released_at"),
        "rarity":        data.get("rarity"),
        "colors":        data.get("colors", []),
        "color_identity": data.get("color_identity", []),
        "mana_cost":     mana_cost,
        "cmc":           data.get("cmc", 0),
        "type_line":     type_line,
        "oracle_text":   oracle_text,
        "flavor_text":   data.get("flavor_text"),
        "power":         power,
        "toughness":     toughness,
        "loyalty":       data.get("loyalty"),
        "keywords":      data.get("keywords", []),
        "legalities":    data.get("legalities", {}),
        "price_usd":     _safe_float(prices.get("usd")),
        "price_eur":     _safe_float(prices.get("eur")),
        "price_tix":     _safe_float(prices.get("tix")),
        "image_url":     image_url,
        "image_url_back": image_url_back,
        "language":      lang,
    }


def _safe_float(v) -> Optional[float]:
    try:
        return float(v) if v else None
    except (TypeError, ValueError):
        return None


class ScryfallClient:
    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        self._last_request = 0.0
        self._semaphore = asyncio.Semaphore(1)
        # (card_dict, data_fetched_at, price_fetched_at)
        self._id_cache: dict[str, tuple[dict, float, float]] = {}
        # name/autocomplete result cache: arg-tuple → (result, fetched_at)
        self._name_cache: dict[tuple, tuple] = {}

    async def _session_get(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(headers=_HEADERS)
        return self._session

    async def _get(self, url: str, **params) -> Optional[dict]:
        async with self._semaphore:
            loop = asyncio.get_running_loop()
            await asyncio.sleep(max(0, 0.1 - (loop.time() - self._last_request)))
            session = await self._session_get()
            for attempt in range(3):
                try:
                    async with session.get(url, params=params, timeout=_REQUEST_TIMEOUT) as resp:
                        self._last_request = loop.time()
                        if resp.status == 200:
                            return await resp.json()
                        if resp.status == 404:
                            return None
                        if resp.status == 429:
                            wait = float(resp.headers.get("Retry-After", 1.0))
                            logger.warning("Scryfall 429 — retrying in %.1fs (attempt %d/3)", wait, attempt + 1)
                            await asyncio.sleep(wait)
                            continue
                        if resp.status >= 500:
                            logger.warning("Scryfall %s → %s (attempt %d/3)", url, resp.status, attempt + 1)
                            await asyncio.sleep(1.0 * (attempt + 1))
                            continue
                        logger.warning("Scryfall %s → %s", url, resp.status)
                        return None
                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    logger.warning("Scryfall request error: %s (attempt %d/3)", e, attempt + 1)
                    if attempt < 2:
                        await asyncio.sleep(1.0 * (attempt + 1))
            logger.error("Scryfall %s — gave up after 3 attempts", url)
            return None

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    # ------------------------------------------------------------------ #

    def _ncache_get(self, key: tuple, ttl: float):
        """Return (True, value) if key is cached and unexpired, else (False, None)."""
        entry = self._name_cache.get(key)
        if entry is not None:
            value, ts = entry
            if time.monotonic() - ts < ttl:
                return True, value
        return False, None

    def _ncache_set(self, key: tuple, value) -> None:
        self._name_cache[key] = (value, time.monotonic())

    async def get_by_name(self, name: str, fuzzy: bool = True, set_code: Optional[str] = None) -> Optional[dict]:
        """Fetch English card by name. Returns normalized dict or None."""
        key = ("byname", name.lower().strip(), fuzzy, set_code or "")
        hit, cached = self._ncache_get(key, _CARD_DATA_TTL)
        if hit:
            return cached
        params = {"fuzzy" if fuzzy else "exact": name}
        if set_code:
            params["set"] = set_code
        data = await self._get(f"{BASE}/cards/named", **params)
        result = _extract_card(data) if data and data.get("object") == "card" else None
        self._ncache_set(key, result)
        return result

    async def get_german(self, name: str, set_code: Optional[str] = None) -> Optional[dict]:
        """Search for a German-language printing by printed name."""
        key = ("german", name.lower().strip(), set_code or "")
        hit, cached = self._ncache_get(key, _CARD_DATA_TTL)
        if hit:
            return cached
        # Validate set_code to prevent query injection via Scryfall search syntax
        safe_set = set_code if (set_code and _SET_CODE_RE.match(set_code)) else None
        set_filter = f" set:{safe_set}" if safe_set else ""
        # Escape quotes in name so user input cannot break out of the quoted term
        safe_name = name.replace('"', '')
        # Try exact German printed name search
        data = await self._get(
            f"{BASE}/cards/search",
            q=f'lang:de "{safe_name}"{set_filter}',
            order="released",
        )
        if data and data.get("data"):
            result = _extract_card(data["data"][0])
            self._ncache_set(key, result)
            return result
        # Fuzzy fallback — strip any remaining Scryfall operator characters
        safe_name_fuzzy = re.sub(r"[^\w\s\-']", "", safe_name)
        data = await self._get(
            f"{BASE}/cards/search",
            q=f"lang:de {safe_name_fuzzy}{set_filter}",
            order="released",
        )
        if data and data.get("data"):
            result = _extract_card(data["data"][0])
            self._ncache_set(key, result)
            return result
        self._ncache_set(key, None)
        return None

    async def get_by_id(self, scryfall_id: str, force_refresh: bool = False) -> Optional[dict]:
        now = time.monotonic()
        if not force_refresh and scryfall_id in self._id_cache:
            card, data_ts, price_ts = self._id_cache[scryfall_id]
            if now - data_ts < _CARD_DATA_TTL:
                if now - price_ts < _PRICE_TTL:
                    return card  # both fresh — no network call
                # Card data still valid; only prices are stale — lightweight refresh
                raw = await self._get(f"{BASE}/cards/{scryfall_id}")
                if raw and raw.get("object") == "card":
                    prices = raw.get("prices", {})
                    card = dict(card)
                    card["price_eur"] = _safe_float(prices.get("eur"))
                    card["price_usd"] = _safe_float(prices.get("usd"))
                    card["price_tix"] = _safe_float(prices.get("tix"))
                    self._id_cache[scryfall_id] = (card, data_ts, now)
                return card
        # Full fetch — card data missing or older than 30 days
        data = await self._get(f"{BASE}/cards/{scryfall_id}")
        if data and data.get("object") == "card":
            card = _extract_card(data)
            self._id_cache[scryfall_id] = (card, now, now)
            return card
        return None

    async def get_by_collector(self, set_code: str, collector_number: str, lang: str = "en") -> Optional[dict]:
        data = await self._get(f"{BASE}/cards/{set_code.lower()}/{collector_number}/{lang}")
        if data and data.get("object") == "card":
            return _extract_card(data)
        return None

    async def search_cards(
        self,
        name: str = "",
        set_code: Optional[str] = None,
        lang: Optional[str] = None,
    ) -> list[dict]:
        """Search Scryfall with any combination of name, set and language.

        Name is optional — if omitted, filters must narrow the result enough.
        Tries an exact name match first, then falls back to a fuzzy/partial search.
        Returns up to 50 distinct card printings.
        """
        safe_name = name.replace('"', "").strip()
        safe_set = set_code if (set_code and _SET_CODE_RE.match(set_code)) else None
        safe_lang = lang if lang else None

        # Build the non-name filter string once
        extras = []
        if safe_set:
            extras.append(f"set:{safe_set}")
        if safe_lang:
            extras.append(f"lang:{safe_lang}")
        extra_str = " ".join(extras)

        async def _search(q: str) -> list[dict]:
            data = await self._get(
                f"{BASE}/cards/search",
                q=q.strip(),
                unique="cards",
                order="released",
                dir="desc",
            )
            if data and data.get("data"):
                return [_extract_card(c) for c in data["data"][:50]]
            return []

        if safe_name:
            # Exact name first
            results = await _search(f'!"{safe_name}" {extra_str}')
            if results:
                return results
            # Partial / fuzzy fallback
            safe_fuzzy = re.sub(r'[^\w\s\-\']', "", safe_name)
            results = await _search(f"{safe_fuzzy} {extra_str}")
            if results:
                return results
        elif extra_str:
            # No name — search by filters only (e.g. all cards in a set/lang)
            results = await _search(extra_str)
            if results:
                return results

        return []

    async def autocomplete(self, query: str) -> list[str]:
        """Return up to 20 Scryfall card name suggestions for a prefix query."""
        q = query.strip()
        if not q or len(q) < 2:
            return []
        key = ("autocomplete", q.lower())
        hit, cached = self._ncache_get(key, _CARD_DATA_TTL)
        if hit:
            return cached
        data = await self._get(f"{BASE}/cards/autocomplete", q=q)
        result = data["data"] if data and isinstance(data.get("data"), list) else []
        self._ncache_set(key, result)
        return result

    async def resolve_card(self, name: str, set_code: Optional[str] = None) -> tuple[Optional[dict], str]:
        """
        Try German first, then English, then return (None, 'unknown') to trigger user input.
        """
        # German attempt first
        card = await self.get_german(name, set_code=set_code)
        if card:
            en_card = await self.get_by_name(card["name_en"])
            if en_card:
                card["name_de"] = card.get("printed_name")
                card["name_en"] = en_card["name_en"]
                if not card.get("price_eur"):
                    card["price_eur"] = en_card.get("price_eur")
                if not card.get("price_usd"):
                    card["price_usd"] = en_card.get("price_usd")
            else:
                logger.warning("resolve_card: English lookup failed for '%s'", card["name_en"])
            card["language"] = "de"
            return card, "de"

        # English fallback
        card = await self.get_by_name(name, fuzzy=True, set_code=set_code)
        if card:
            return card, "en"

        return None, "unknown"
