"""Cardmarket price client via RapidAPI.

Credentials are read from the environment:
  RAPIDAPI_KEY   — your RapidAPI subscription key  (X-RapidAPI-Key header)
  RAPIDAPI_HOST  — the Cardmarket API host on RapidAPI (X-RapidAPI-Host header)
"""
from __future__ import annotations

import logging
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

_GAME_MTG = 1


class CardmarketClient:
    def __init__(self, api_key: str, api_host: str):
        self._api_key = api_key
        self._api_host = api_host
        self._base = f"https://{api_host}"
        self._session: Optional[aiohttp.ClientSession] = None
        # (name_lower, set_lower) → product_id
        self._id_cache: dict[tuple[str, str], Optional[int]] = {}

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "X-RapidAPI-Key": self._api_key,
            "X-RapidAPI-Host": self._api_host,
        }

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30)
            )
        return self._session

    async def _get(self, path: str, params: dict | None = None) -> Optional[dict]:
        url = f"{self._base}{path}"
        session = await self._ensure_session()
        try:
            async with session.get(url, params=params or {}, headers=self._headers) as resp:
                if resp.status == 200:
                    return await resp.json(content_type=None)
                body = await resp.text()
                logger.warning("Cardmarket %s → HTTP %s: %s", path, resp.status, body[:300])
                return None
        except Exception as exc:
            logger.error("Cardmarket request error: %s", exc)
            return None

    # ------------------------------------------------------------------ #
    # Public API                                                            #
    # ------------------------------------------------------------------ #

    async def find_product_id(self, name_en: str, set_code: str) -> Optional[int]:
        key = (name_en.lower(), set_code.lower())
        if key in self._id_cache:
            return self._id_cache[key]

        data = await self._get(
            "/products/find",
            {
                "search": name_en,
                "exact": "true",
                "idGame": str(_GAME_MTG),
                "idLanguage": "1",
            },
        )

        products: list[dict] = []
        if data:
            raw = data.get("product") or []
            products = [raw] if isinstance(raw, dict) else list(raw)

        set_up = set_code.upper()
        matched_id: Optional[int] = None
        for p in products:
            exp_abbr = (p.get("expansion") or {}).get("abbreviation", "").upper()
            if exp_abbr == set_up:
                matched_id = p.get("idProduct")
                break
        if matched_id is None and products:
            matched_id = products[0].get("idProduct")

        self._id_cache[key] = matched_id
        return matched_id

    async def get_price(
        self, name_en: str, set_code: str, foil: bool = False
    ) -> Optional[float]:
        """Return TREND EUR price (or AVG fallback) from the Cardmarket price guide."""
        product_id = await self.find_product_id(name_en, set_code)
        if product_id is None:
            logger.debug("Cardmarket: no product for '%s' (%s)", name_en, set_code)
            return None

        data = await self._get(f"/products/{product_id}")
        if not data:
            return None

        guide: dict = (data.get("product") or {}).get("priceGuide") or {}
        raw = guide.get("TRENDFOIL" if foil else "TREND") or guide.get(
            "AVGFOIL" if foil else "AVG"
        )
        try:
            return float(raw) if raw is not None else None
        except (TypeError, ValueError):
            return None

    async def test_connection(self) -> tuple[bool, str]:
        """Verify credentials by calling /account."""
        data = await self._get("/account")
        if data is None:
            return False, "Connection failed — check RAPIDAPI_KEY / RAPIDAPI_HOST and network."
        account = data.get("account") or {}
        username = account.get("username", "?")
        return True, f"Connected as '{username}'"

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
