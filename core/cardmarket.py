"""Cardmarket price client via RapidAPI (cardmarket-api-tcg.p.rapidapi.com).

Credentials from environment:
  RAPIDAPI_KEY   — your RapidAPI subscription key
  RAPIDAPI_HOST  — cardmarket-api-tcg.p.rapidapi.com

Endpoint: GET /magic/cards?search={name}&rapidapi-key={key}
Response: {"data": [...], "paging": {...}, "results": N}
"""
from __future__ import annotations

import json
import logging
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

_MTG_PATH = "/magic/cards"

# Price fields to try in order of preference
_PRICE_FIELDS = (
    "price", "priceEur", "price_eur", "eur",
    "trendPrice", "trend_price",
    "avgPrice", "avg_price",
    "lowestPrice", "lowest_price",
    "sellingPrice", "sell_price",
)


class CardmarketClient:
    def __init__(self, api_key: str, api_host: str):
        self._api_key = api_key
        self._api_host = api_host
        self._base = f"https://{api_host}"
        self._session: Optional[aiohttp.ClientSession] = None
        # cache_key → price_eur
        self._price_cache: dict[str, Optional[float]] = {}

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "X-RapidAPI-Key": self._api_key,
            "X-RapidAPI-Host": self._api_host,
        }

    def _params(self, extra: dict | None = None) -> dict:
        p: dict = {"rapidapi-key": self._api_key}
        if extra:
            p.update(extra)
        return p

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30)
            )
        return self._session

    async def _get_raw(self, path: str, params: dict | None = None) -> tuple[int, str]:
        url = f"{self._base}{path}"
        session = await self._ensure_session()
        try:
            async with session.get(
                url, params=self._params(params), headers=self._headers
            ) as resp:
                return resp.status, await resp.text()
        except Exception as exc:
            return 0, str(exc)

    async def _search(self, name: str) -> list[dict]:
        """Search /magic/cards and return the data array."""
        status, body = await self._get_raw(_MTG_PATH, {"search": name})
        if status != 200:
            logger.warning("Cardmarket search '%s' → HTTP %s: %s", name, status, body[:200])
            return []
        try:
            data = json.loads(body)
        except Exception:
            return []
        cards = data.get("data") or []
        return cards if isinstance(cards, list) else []

    # ------------------------------------------------------------------ #
    # Public API                                                            #
    # ------------------------------------------------------------------ #

    async def get_price(
        self, name_en: str, set_code: str, foil: bool = False
    ) -> Optional[float]:
        cache_key = f"{name_en.lower()}|{set_code.lower()}|{foil}"
        if cache_key in self._price_cache:
            return self._price_cache[cache_key]

        cards = await self._search(name_en)
        price = _pick_price(cards, name_en, set_code, foil)
        self._price_cache[cache_key] = price
        return price

    async def test_connection(self) -> tuple[bool, str]:
        """Probe the API systematically and return a diagnostic string."""
        lines: list[str] = []

        def _probe(label: str, status: int, body: str) -> bool:
            """Log the probe; return True if results were found."""
            snippet = body[:300].replace("\n", " ")
            lines.append(f"{label} → HTTP {status}: {snippet}")
            if status == 200:
                try:
                    d = json.loads(body)
                    n = d.get("results") or len(d.get("data") or [])
                    lines[-1] += f"  [results={n}]"
                    if d.get("data"):
                        first = d["data"][0]
                        lines.append(f"  keys: {list(first.keys())}")
                        lines.append(f"  first: {json.dumps(first)[:400]}")
                        return True
                except Exception:
                    pass
            return False

        # 1. List all cards with no filter
        st, body = await self._get_raw(_MTG_PATH)
        if _probe("GET /magic/cards (no params)", st, body):
            return True, "\n".join(lines)

        # 2. Alternative parameter names for the search term
        for param in ("name", "q", "cardname", "card_name", "keyword"):
            st, body = await self._get_raw(_MTG_PATH, {param: "Lightning Bolt"})
            if _probe(f"/magic/cards?{param}=Lightning+Bolt", st, body):
                return True, "\n".join(lines)

        # 3. Alternative path patterns
        for path in ("/mtg/cards", "/magic/card", "/cards", "/magic"):
            st, body = await self._get_raw(path)
            if _probe(f"GET {path} (no params)", st, body):
                return True, "\n".join(lines)

        return False, "\n".join(lines)

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None


def _pick_price(
    cards: list[dict],
    name_en: str,
    set_code: str,
    foil: bool,
) -> Optional[float]:
    if not cards:
        return None

    name_lo = name_en.lower()
    set_up = set_code.upper()

    # Rank: exact name + exact set > exact name > first result
    best: Optional[dict] = None
    for card in cards:
        cname = str(
            card.get("name") or card.get("cardName") or card.get("title") or ""
        ).lower()
        if cname != name_lo:
            continue
        exp = str(
            card.get("expansion") or card.get("set") or card.get("setCode") or card.get("set_code") or ""
        ).upper()
        if exp == set_up:
            best = card
            break
        if best is None:
            best = card

    if best is None and cards:
        best = cards[0]

    if best is None:
        return None

    for field in _PRICE_FIELDS:
        val = best.get(field)
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                pass

    logger.debug("Cardmarket: no recognised price field. Card keys: %s", list(best.keys()))
    return None
