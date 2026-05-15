"""
Scryfall API client.
Rate limit: max 10 req/s — we enforce a 100 ms gap between requests.
"""

import asyncio
import logging
import re
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

BASE = "https://api.scryfall.com"
_HEADERS = {"Accept": "application/json;q=0.9,*/*;q=0.8"}
_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)
_SET_CODE_RE = re.compile(r"^[a-zA-Z0-9]{1,10}$")

# German MTG set codes that are purely German editions (very old)
_DE_SETS = {"por", "ptk", "s99"}


def _extract_card(data: dict, preferred_lang: Optional[str] = None) -> dict:
    """Normalize a Scryfall card object into our DB schema."""
    lang = data.get("lang", "en")

    # Prices
    prices = data.get("prices", {})

    # Image
    images = data.get("image_uris", {})
    faces = data.get("card_faces")
    if not images and faces:
        images = faces[0].get("image_uris", {})
    image_url = images.get("normal") or images.get("small")

    # English vs German name
    printed = data.get("printed_name") or data.get("name")
    if lang == "en":
        name_en = data.get("name", "")
        name_de = None
    else:
        name_en = data.get("name", "")  # Scryfall always gives oracle name
        name_de = data.get("printed_name") if lang == "de" else None

    # For non-English cards use the localized printed fields when available
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
        "scryfall_id": data.get("id"),
        "oracle_id": data.get("oracle_id"),
        "name_en": name_en,
        "name_de": name_de,
        "printed_name": printed,
        "set_code": data.get("set"),
        "set_name": data.get("set_name"),
        "collector_number": data.get("collector_number"),
        "released_at": data.get("released_at"),
        "rarity": data.get("rarity"),
        "colors": data.get("colors", []),
        "color_identity": data.get("color_identity", []),
        "mana_cost": data.get("mana_cost"),
        "cmc": data.get("cmc", 0),
        "type_line": type_line,
        "oracle_text": oracle_text,
        "flavor_text": data.get("flavor_text"),
        "power": data.get("power"),
        "toughness": data.get("toughness"),
        "loyalty": data.get("loyalty"),
        "keywords": data.get("keywords", []),
        "legalities": data.get("legalities", {}),
        "price_usd": _safe_float(prices.get("usd")),
        "price_eur": _safe_float(prices.get("eur")),
        "price_tix": _safe_float(prices.get("tix")),
        "image_url": image_url,
        "language": lang,
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

    async def _session_get(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(headers=_HEADERS)
        return self._session

    async def _get(self, url: str, **params) -> Optional[dict]:
        async with self._semaphore:
            loop = asyncio.get_running_loop()
            await asyncio.sleep(max(0, 0.1 - (loop.time() - self._last_request)))
            session = await self._session_get()
            try:
                async with session.get(url, params=params, timeout=_REQUEST_TIMEOUT) as resp:
                    self._last_request = loop.time()
                    if resp.status == 200:
                        return await resp.json()
                    if resp.status == 404:
                        return None
                    logger.warning("Scryfall %s → %s", url, resp.status)
                    return None
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.error("Scryfall request failed: %s", e)
                return None

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    # ------------------------------------------------------------------ #

    async def get_by_name(self, name: str, fuzzy: bool = True, set_code: Optional[str] = None) -> Optional[dict]:
        """Fetch English card by name. Returns normalized dict or None."""
        params = {"fuzzy" if fuzzy else "exact": name}
        if set_code:
            params["set"] = set_code
        data = await self._get(f"{BASE}/cards/named", **params)
        if data and data.get("object") == "card":
            return _extract_card(data)
        return None

    async def get_german(self, name: str, set_code: Optional[str] = None) -> Optional[dict]:
        """Search for a German-language printing by printed name."""
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
            return _extract_card(data["data"][0])
        # Fuzzy fallback — strip any remaining Scryfall operator characters
        safe_name_fuzzy = re.sub(r"[^\w\s\-']", "", safe_name)
        data = await self._get(
            f"{BASE}/cards/search",
            q=f"lang:de {safe_name_fuzzy}{set_filter}",
            order="released",
        )
        if data and data.get("data"):
            return _extract_card(data["data"][0])
        return None

    async def get_by_id(self, scryfall_id: str) -> Optional[dict]:
        data = await self._get(f"{BASE}/cards/{scryfall_id}")
        if data and data.get("object") == "card":
            return _extract_card(data)
        return None

    async def get_by_collector(self, set_code: str, collector_number: str, lang: str = "en") -> Optional[dict]:
        data = await self._get(f"{BASE}/cards/{set_code.lower()}/{collector_number}/{lang}")
        if data and data.get("object") == "card":
            return _extract_card(data)
        return None

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
